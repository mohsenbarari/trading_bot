#!/usr/bin/env python3
"""Build one immutable frozen-final restore-set closure.

The default invocation is plan-only.  This builder performs local, read-only
validation and never contacts Object Storage or a production host.  Apply mode
creates exactly one canonical root-only manifest below a digest-derived
namespace; it does not install artifacts or execute a restore worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import CanonicalJSONError, canonical_json_bytes
from core.production_shadow_authorization import (
    ProductionShadowAuthorizationError,
    verify_authorization_documents,
)
from core.secure_file_io import (
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from scripts import orchestrate_production_shadow_nginx_generations as NGINX
from scripts import produce_production_shadow_source_snapshot as SOURCE
from scripts.build_production_shadow_source_snapshot_binding import build_binding
from scripts.production_shadow_cutover_controller import (
    CutoverContractError,
    read_root_only_manifest,
)


SCHEMA = "production-shadow-frozen-final-restore-set-v1"
IR_TRANSPORT_SCHEMA = "production-shadow-frozen-final-ir-transport-v1"
IR_READBACK_SCHEMA = (
    "production-shadow-frozen-final-ir-object-readback-v1"
)
LIVE_LEASE_CLAIM_SCHEMA = (
    "production-shadow-nginx-coordinator-live-lease-claim-v1"
)
SOURCE_FREEZE_EVIDENCE_SCHEMA = (
    "production-shadow-source-freeze-evidence-v2"
)
OUTPUT_FILENAME = "frozen-final-restore-set.json"
CONTROLLER_SECRET_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[023456789acdefghjklmnpqrstuvwxyz]{20,100}$")
LEASE_NONCE_RE = re.compile(r"^[0-9a-f]{64}$")
TARGET_MAP = {
    "bot_fi": {
        "source_role": "bot_fi",
        "transport": "host-local-create-only",
    },
    "webapp_fi": {
        "source_role": "webapp_fi",
        "transport": "ssh-control",
    },
    "webapp_ir": {
        "source_role": "webapp_fi",
        "transport": "arvan-private-versioned-age",
    },
}
SOURCE_ROLES = ("bot_fi", "webapp_fi")
SOURCE_ARTIFACTS = (
    "database-backup",
    "uploads-archive",
    "audit-archive",
)
RESTORE_SET_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "legacy_release_sha",
        "controller_manifest_sha256",
        "approval_sha256",
        "approval_policy_sha256",
        "restore_generation_sha256",
        "target_map",
        "sources",
        "postgres_snapshot_set_sha256",
        "reviewed_file_snapshot_set_sha256",
        "nginx_freeze",
        "snapshot_authorization_claim",
        "webapp_ir_transport",
        "constraints",
    }
)
SOURCE_RESTORE_FIELDS = frozenset(
    {
        "source_snapshot_manifest_sha256",
        "source_snapshot_binding_sha256",
        "freeze_evidence_sha256",
        "live_lease_claim_sha256",
        "source_identity_sha256",
        "artifacts",
        "source_database",
        "restore_input_sha256",
        "freeze_generation_sha256",
        "source_container_ids",
        "restore_drill_sha256",
        "redis_rollback_metadata_sha256",
        "redis_restore_included",
    }
)
NGINX_FREEZE_FIELDS = frozenset(
    {
        "state",
        "aggregate_sha256",
        "state_receipt_sha256",
        "global_generation_sha256",
        "role_generation_sha256",
        "role_bindings",
        "journal_sha256",
        "journal_sequence",
        "journal_tail_sha256",
        "external_readback_sha256",
    }
)
SNAPSHOT_AUTHORIZATION_CLAIM_OUTPUT_FIELDS = frozenset(
    {
        "claim_sha256",
        "claim_epoch",
        "previous_claim_sha256",
        "nonce",
        "owner_action",
        "claim_document_status",
        "controller_lock_path_at_issue",
        "legacy_frozen_receipt_sha256",
        "receipt_journal_sha256",
        "receipt_journal_sequence",
        "receipt_journal_tail_sha256",
        "controller_journal_event_count",
        "claim_declared_controller_authoritative_at_issue",
        "copied_material_authoritative",
        "automatic_expiry_allowed",
        "reconciliation_required_after_crash",
        "claim_liveness_asserted",
        "future_install_or_restore_authority_implied",
        "fresh_live_authority_required_before_install_or_restore",
    }
)
IR_TRANSPORT_OUTPUT_FIELDS = frozenset(
    {
        "transport_manifest_sha256",
        "readback_receipt_sha256",
        "provider",
        "bucket",
        "private",
        "versioned",
        "encryption",
        "recipient",
        "plaintext_restore_input_set_sha256",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "object_key",
        "version_id",
        "exact_version_readback_verified",
    }
)
CONSTRAINT_FIELDS = frozenset(
    {
        "plan_only_default",
        "network_io_performed",
        "object_storage_contacted",
        "production_contacted",
        "installer_executed",
        "restore_worker_executed",
        "service_mutated",
        "current_mutated",
        "container_mutated",
        "volume_mutated",
        "data_mutated",
        "legacy_redis_restore_included",
        "snapshot_authorization_claim_copy_is_not_live_authority",
        "snapshot_authorization_claim_liveness_asserted",
        "future_install_or_restore_authority_implied",
        "fresh_live_authority_required_before_install_or_restore",
    }
)
LIVE_LEASE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "owner_action",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "aggregate_sha256",
        "claim_epoch",
        "previous_claim_sha256",
        "nonce",
        "controller_pid",
        "controller_lock_path",
        "controller_authoritative",
        "remote_copy_authoritative",
        "automatic_expiry_allowed",
        "reconciliation_required_after_crash",
        "legacy_frozen_receipt_path",
        "legacy_frozen_receipt_sha256",
        "receipt_journal_sha256",
        "receipt_journal_sequence",
        "receipt_journal_tail_sha256",
        "controller_journal_event_count",
        "receipt_state",
        "receipt_global_generation_sha256",
        "receipt_role_generation_sha256",
        "receipt_role_bindings",
        "receipt_readbacks",
    }
)
IR_TRANSPORT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "approval_sha256",
        "source_role",
        "target_role",
        "provider",
        "bucket",
        "private",
        "versioned",
        "encryption",
        "recipient",
        "plaintext_restore_input_set_sha256",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "object_key",
        "version_id",
        "readback_receipt_sha256",
    }
)
IR_READBACK_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "source_role",
        "target_role",
        "provider",
        "bucket",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "exact_version_requested",
        "body_sha256",
        "body_bytes",
    }
)


class FrozenFinalRestoreSetError(RuntimeError):
    """The frozen-final restore closure could not be proven exact."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FrozenFinalRestoreSetError(
                f"duplicate JSON field is forbidden: {key}"
            )
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return canonical_json_bytes(value)
    except CanonicalJSONError as exc:
        raise FrozenFinalRestoreSetError(
            "restore-set data is not canonical JSON"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise FrozenFinalRestoreSetError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _bounded_bytes(value: Any, *, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_ARTIFACT_BYTES
    ):
        raise FrozenFinalRestoreSetError(
            f"{label} byte count is outside its bound"
        )
    return value


def _bounded_opaque(value: Any, *, label: str, maximum: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= maximum
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise FrozenFinalRestoreSetError(f"{label} is invalid")
    return value


def _read_canonical_json(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_JSON_BYTES,
) -> tuple[dict[str, Any], bytes, str]:
    if not path.is_absolute():
        raise FrozenFinalRestoreSetError(f"{label} path must be absolute")
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=maximum,
        )
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except FrozenFinalRestoreSetError:
        raise
    except (SecureFileError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrozenFinalRestoreSetError(
            f"{label} is not secure strict JSON"
        ) from exc
    if (
        not isinstance(document, dict)
        or payload != _canonical_json(document)
    ):
        raise FrozenFinalRestoreSetError(f"{label} is not canonical JSON")
    return document, payload, _sha256(payload)


def _load_controller(
    path: Path,
    *,
    approval_path: Path,
    approval_policy_path: Path,
) -> tuple[dict[str, Any], str, str, str]:
    try:
        controller, controller_sha256 = read_root_only_manifest(
            path,
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        controller_raw = read_secure_bytes(
            path,
            label="production cutover manifest",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        approval_raw = read_secure_bytes(
            approval_path,
            label="production cutover approval",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        policy_raw = read_secure_bytes(
            approval_policy_path,
            label="production human approval policy",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        verified = verify_authorization_documents(
            controller,
            approval_bytes=approval_raw,
            policy_bytes=policy_raw,
            require_fresh=True,
        )
    except (
        CutoverContractError,
        ProductionShadowAuthorizationError,
        SecureFileError,
    ) as exc:
        raise FrozenFinalRestoreSetError(
            "controller manifest or live approval is invalid"
        ) from exc
    if (
        controller_raw != _canonical_json(controller)
        or _sha256(controller_raw) != controller_sha256
        or verified.token_hash
        != controller["artifacts"]["cutover_approval_sha256"]
    ):
        raise FrozenFinalRestoreSetError(
            "controller manifest or approval canonical identity differs"
        )
    return (
        controller,
        controller_sha256,
        verified.token_hash,
        _sha256(policy_raw),
    )


def _snapshot_binding(
    controller: Mapping[str, Any],
    *,
    controller_sha256: str,
    role: str,
) -> SOURCE.SnapshotBinding:
    try:
        document = build_binding(
            controller,
            controller_sha256=controller_sha256,
            role=role,
            mode="frozen-final",
        )
    except Exception as exc:
        raise FrozenFinalRestoreSetError(
            f"{role} frozen-final binding could not be derived"
        ) from exc
    digest = _sha256(_canonical_json(document))
    return SOURCE.SnapshotBinding(
        operation_id=document["operation_id"],
        release_sha=document["release_sha"],
        legacy_release_sha=document["legacy_release_sha"],
        role=document["role"],
        source_project=document["source_project"],
        containers=dict(document["containers"]),
        images=dict(document["images"]),
        volumes=dict(document["volumes"]),
        controller_manifest_sha256=document[
            "controller_manifest_sha256"
        ],
        approval_sha256=document["approval_sha256"],
        mode=document["mode"],
        canonical_sha256=digest,
    )


def _load_frozen_source(
    *,
    role: str,
    manifest_path: Path,
    freeze_evidence_path: Path,
    controller: Mapping[str, Any],
    controller_sha256: str,
) -> dict[str, Any]:
    if (
        manifest_path.name != SOURCE.MANIFEST_FILE
        or manifest_path.parent.name != "frozen-final"
    ):
        raise FrozenFinalRestoreSetError(
            f"{role} source manifest path is not a frozen-final output"
        )
    binding = _snapshot_binding(
        controller,
        controller_sha256=controller_sha256,
        role=role,
    )
    preliminary, manifest_raw, manifest_sha256 = _read_canonical_json(
        manifest_path,
        label=f"{role} frozen-final source manifest",
    )
    if (
        preliminary.get("role") != role
        or preliminary.get("mode") != "frozen-final"
        or preliminary.get("binding_sha256") != binding.canonical_sha256
    ):
        raise FrozenFinalRestoreSetError(
            f"{role} source manifest is not the derived frozen-final binding"
        )
    source = preliminary.get("source")
    container_rows = source.get("containers") if isinstance(source, dict) else None
    if not isinstance(container_rows, dict):
        raise FrozenFinalRestoreSetError(
            f"{role} source container inventory is unavailable"
        )
    container_ids = {
        kind: row.get("id") if isinstance(row, dict) else None
        for kind, row in container_rows.items()
    }
    try:
        freeze, freeze_sha256 = SOURCE.load_freeze_evidence(
            freeze_evidence_path,
            binding,
            source_container_ids=container_ids,
        )
        paths = SOURCE.OutputPaths(
            operation_root=manifest_path.parent.parent.parent,
            role_root=manifest_path.parent.parent,
            final=manifest_path.parent,
            staging=manifest_path.parent.parent / ".frozen-final.incomplete",
            manifest=manifest_path,
        )
        document = SOURCE.verify_completed_output(
            paths,
            binding,
            freeze_sha256=freeze_sha256,
        )
    except SOURCE.SourceSnapshotError as exc:
        raise FrozenFinalRestoreSetError(
            f"{role} frozen-final source closure is invalid"
        ) from exc
    if document != preliminary or manifest_raw != _canonical_json(document):
        raise FrozenFinalRestoreSetError(
            f"{role} source manifest changed during verification"
        )
    if freeze.get("schema") != SOURCE_FREEZE_EVIDENCE_SCHEMA:
        raise FrozenFinalRestoreSetError(
            f"{role} source freeze evidence is not schema v2"
        )
    live_lease_claim_sha256 = _nonzero_sha256(
        freeze.get("live_lease_claim_sha256"),
        label=f"{role} snapshot authorization live-lease claim",
    )
    artifacts = {
        kind: dict(document["artifacts"][kind])
        for kind in SOURCE_ARTIFACTS
    }
    restore_input = {
        "source_snapshot_manifest_sha256": manifest_sha256,
        "source_snapshot_binding_sha256": binding.canonical_sha256,
        "freeze_evidence_sha256": freeze_sha256,
        "live_lease_claim_sha256": live_lease_claim_sha256,
        "source_identity_sha256": document["source"]["identity_sha256"],
        "artifacts": artifacts,
        "source_database": dict(document["source_database"]),
    }
    return {
        **restore_input,
        "restore_input_sha256": _sha256(_canonical_json(restore_input)),
        "freeze_generation_sha256": freeze["freeze_generation_sha256"],
        "source_container_ids": dict(sorted(freeze["source_container_ids"].items())),
        "restore_drill_sha256": _sha256(
            _canonical_json(document["restore_drill"])
        ),
        "redis_rollback_metadata_sha256": _sha256(
            _canonical_json(document["redis_rollback_only"])
        ),
        "redis_restore_included": False,
    }


def _canonical_controller_path(
    operation_id: str,
    *parts: str,
) -> Path:
    return CONTROLLER_SECRET_PREFIX / operation_id / "nginx-coordinator" / Path(*parts)


def _load_freeze_receipt(
    path: Path,
    *,
    controller: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    preliminary, _raw, _digest = _read_canonical_json(
        path,
        label="coordinated legacy-frozen state receipt",
    )
    aggregate_sha256 = _nonzero_sha256(
        preliminary.get("aggregate_sha256"),
        label="Nginx aggregate",
    )
    try:
        receipt, receipt_sha256 = NGINX.load_state_receipt(
            path,
            "legacy-frozen",
            controller["operation_id"],
            controller["release_sha"],
            controller["release_tree_sha"],
            aggregate_sha256,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise FrozenFinalRestoreSetError(
            "coordinated legacy-frozen receipt is invalid"
        ) from exc
    expected_path = _canonical_controller_path(
        controller["operation_id"],
        "receipts",
        f"legacy-frozen-{receipt_sha256}.json",
    )
    if (
        path != expected_path
        or receipt["global_generation_sha256"]
        != controller["artifacts"]["nginx_freeze_generation_sha256"]
    ):
        raise FrozenFinalRestoreSetError(
            "legacy-frozen receipt path or controller generation differs"
        )
    return receipt, receipt_sha256


def _load_snapshot_authorization_claim(
    path: Path,
    *,
    controller: Mapping[str, Any],
    receipt: Mapping[str, Any],
    receipt_path: Path,
    receipt_sha256: str,
) -> tuple[dict[str, Any], str]:
    document, _raw, digest = _read_canonical_json(
        path,
        label="snapshot-authorization live-lease claim",
    )
    operation_id = controller["operation_id"]
    expected_claim_path = _canonical_controller_path(
        operation_id,
        "live-leases",
        "claims",
        f"{digest}.json",
    )
    expected_receipt_path = _canonical_controller_path(
        operation_id,
        "receipts",
        f"legacy-frozen-{receipt_sha256}.json",
    )
    role_generation = {
        role: receipt["readbacks"][role]["generation_sha256"]
        for role in NGINX.ROLE_ORDER
    }
    previous = document.get("previous_claim_sha256")
    epoch = document.get("claim_epoch")
    if (
        path != expected_claim_path
        or receipt_path != expected_receipt_path
        or set(document) != LIVE_LEASE_FIELDS
        or document["schema"] != LIVE_LEASE_CLAIM_SCHEMA
        or document["status"] != "active"
        or document["owner_action"] != "capture-frozen-final-snapshots"
        or document["operation_id"] != operation_id
        or document["release_sha"] != controller["release_sha"]
        or document["release_tree_sha"] != controller["release_tree_sha"]
        or document["aggregate_sha256"] != receipt["aggregate_sha256"]
        or type(epoch) is not int
        or epoch < 1
        or (
            epoch == 1
            and previous != "0" * 64
        )
        or (
            epoch > 1
            and (
                not isinstance(previous, str)
                or SHA256_RE.fullmatch(previous) is None
                or previous == "0" * 64
            )
        )
        or not isinstance(document["nonce"], str)
        or LEASE_NONCE_RE.fullmatch(document["nonce"]) is None
        or document["nonce"] == "0" * 64
        or type(document["controller_pid"]) is not int
        or document["controller_pid"] < 1
        or document["controller_lock_path"]
        != os.fspath(_canonical_controller_path(operation_id, "coordinator.lock"))
        or document["controller_authoritative"] is not True
        or document["remote_copy_authoritative"] is not False
        or document["automatic_expiry_allowed"] is not False
        or document["reconciliation_required_after_crash"] is not True
        or document["legacy_frozen_receipt_path"]
        != os.fspath(expected_receipt_path)
        or document["legacy_frozen_receipt_sha256"] != receipt_sha256
        or document["receipt_journal_sha256"] != receipt["journal_sha256"]
        or document["receipt_journal_sequence"] != receipt["evidence_count"]
        or document["receipt_journal_tail_sha256"]
        != receipt["evidence_tail_sha256"]
        or type(document["controller_journal_event_count"]) is not int
        or document["controller_journal_event_count"]
        < receipt["evidence_count"]
        or document["receipt_state"] != "legacy-frozen"
        or document["receipt_global_generation_sha256"]
        != receipt["global_generation_sha256"]
        or document["receipt_role_generation_sha256"] != role_generation
        or document["receipt_role_bindings"] != receipt["role_bindings"]
        or document["receipt_readbacks"] != receipt["readbacks"]
    ):
        raise FrozenFinalRestoreSetError(
            "snapshot-authorization claim differs from the frozen receipt"
        )
    return document, digest


def _safe_object_key(value: Any) -> str:
    value = _bounded_opaque(value, label="IR Object Storage key")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or value.endswith("/"):
        raise FrozenFinalRestoreSetError(
            "IR Object Storage key is unsafe"
        )
    return value


def _load_ir_transport(
    transport_path: Path,
    readback_path: Path,
    *,
    controller: Mapping[str, Any],
    controller_sha256: str,
    webapp_fi_restore_input_sha256: str,
) -> dict[str, Any]:
    transport, _transport_raw, transport_sha256 = _read_canonical_json(
        transport_path,
        label="WebApp-IR encrypted transport manifest",
    )
    readback, _readback_raw, readback_sha256 = _read_canonical_json(
        readback_path,
        label="WebApp-IR exact-version readback receipt",
    )
    ciphertext_sha256 = _nonzero_sha256(
        transport.get("ciphertext_sha256"),
        label="IR ciphertext",
    )
    ciphertext_bytes = _bounded_bytes(
        transport.get("ciphertext_bytes"),
        label="IR ciphertext",
    )
    object_key = _safe_object_key(transport.get("object_key"))
    version_id = _bounded_opaque(
        transport.get("version_id"),
        label="IR Object Storage VersionId",
    )
    bucket = _bounded_opaque(
        transport.get("bucket"),
        label="IR Object Storage bucket",
        maximum=255,
    )
    recipient = transport.get("recipient")
    expected_transport = {
        "schema": IR_TRANSPORT_SCHEMA,
        "status": "read-back-verified",
        "operation_id": controller["operation_id"],
        "release_sha": controller["release_sha"],
        "release_tree_sha": controller["release_tree_sha"],
        "controller_manifest_sha256": controller_sha256,
        "approval_sha256": controller["artifacts"][
            "cutover_approval_sha256"
        ],
        "source_role": "webapp_fi",
        "target_role": "webapp_ir",
        "provider": "arvan-s3",
        "bucket": bucket,
        "private": True,
        "versioned": True,
        "encryption": "age",
        "recipient": recipient,
        "plaintext_restore_input_set_sha256": (
            webapp_fi_restore_input_sha256
        ),
        "ciphertext_sha256": ciphertext_sha256,
        "ciphertext_bytes": ciphertext_bytes,
        "object_key": object_key,
        "version_id": version_id,
        "readback_receipt_sha256": readback_sha256,
    }
    if (
        set(transport) != IR_TRANSPORT_FIELDS
        or transport != expected_transport
        or not isinstance(recipient, str)
        or AGE_RECIPIENT_RE.fullmatch(recipient) is None
    ):
        raise FrozenFinalRestoreSetError(
            "WebApp-IR encrypted transport binding differs"
        )
    expected_readback = {
        "schema": IR_READBACK_SCHEMA,
        "status": "read-back-verified",
        "operation_id": controller["operation_id"],
        "release_sha": controller["release_sha"],
        "source_role": "webapp_fi",
        "target_role": "webapp_ir",
        "provider": "arvan-s3",
        "bucket": bucket,
        "object_key": object_key,
        "version_id": version_id,
        "ciphertext_sha256": ciphertext_sha256,
        "ciphertext_bytes": ciphertext_bytes,
        "exact_version_requested": True,
        "body_sha256": ciphertext_sha256,
        "body_bytes": ciphertext_bytes,
    }
    if set(readback) != IR_READBACK_FIELDS or readback != expected_readback:
        raise FrozenFinalRestoreSetError(
            "WebApp-IR exact-version readback receipt differs"
        )
    return {
        "transport_manifest_sha256": transport_sha256,
        "readback_receipt_sha256": readback_sha256,
        "provider": "arvan-s3",
        "bucket": bucket,
        "private": True,
        "versioned": True,
        "encryption": "age",
        "recipient": recipient,
        "plaintext_restore_input_set_sha256": (
            webapp_fi_restore_input_sha256
        ),
        "ciphertext_sha256": ciphertext_sha256,
        "ciphertext_bytes": ciphertext_bytes,
        "object_key": object_key,
        "version_id": version_id,
        "exact_version_readback_verified": True,
    }


def build_restore_set(
    *,
    controller_manifest: Path,
    approval: Path,
    approval_policy: Path,
    bot_fi_source_manifest: Path,
    bot_fi_freeze_evidence: Path,
    webapp_fi_source_manifest: Path,
    webapp_fi_freeze_evidence: Path,
    legacy_frozen_receipt: Path,
    live_lease_claim: Path,
    webapp_ir_transport: Path,
    webapp_ir_readback_receipt: Path,
) -> dict[str, Any]:
    input_paths = (
        controller_manifest,
        approval,
        approval_policy,
        bot_fi_source_manifest,
        bot_fi_freeze_evidence,
        webapp_fi_source_manifest,
        webapp_fi_freeze_evidence,
        legacy_frozen_receipt,
        live_lease_claim,
        webapp_ir_transport,
        webapp_ir_readback_receipt,
    )
    if (
        any(not path.is_absolute() for path in input_paths)
        or len(set(input_paths)) != len(input_paths)
    ):
        raise FrozenFinalRestoreSetError(
            "restore-set inputs must be absolute and distinct"
        )
    (
        controller,
        controller_sha256,
        approval_sha256,
        policy_sha256,
    ) = _load_controller(
        controller_manifest,
        approval_path=approval,
        approval_policy_path=approval_policy,
    )
    sources = {
        "bot_fi": _load_frozen_source(
            role="bot_fi",
            manifest_path=bot_fi_source_manifest,
            freeze_evidence_path=bot_fi_freeze_evidence,
            controller=controller,
            controller_sha256=controller_sha256,
        ),
        "webapp_fi": _load_frozen_source(
            role="webapp_fi",
            manifest_path=webapp_fi_source_manifest,
            freeze_evidence_path=webapp_fi_freeze_evidence,
            controller=controller,
            controller_sha256=controller_sha256,
        ),
    }
    receipt, receipt_sha256 = _load_freeze_receipt(
        legacy_frozen_receipt,
        controller=controller,
    )
    if any(
        source["freeze_generation_sha256"]
        != receipt["global_generation_sha256"]
        for source in sources.values()
    ):
        raise FrozenFinalRestoreSetError(
            "source freeze evidence differs from coordinated Nginx generation"
        )
    claim, claim_sha256 = _load_snapshot_authorization_claim(
        live_lease_claim,
        controller=controller,
        receipt=receipt,
        receipt_path=legacy_frozen_receipt,
        receipt_sha256=receipt_sha256,
    )
    if any(
        source["live_lease_claim_sha256"] != claim_sha256
        for source in sources.values()
    ):
        raise FrozenFinalRestoreSetError(
            "source freeze evidence snapshot-authorization claim differs"
        )
    ir_transport = _load_ir_transport(
        webapp_ir_transport,
        webapp_ir_readback_receipt,
        controller=controller,
        controller_sha256=controller_sha256,
        webapp_fi_restore_input_sha256=sources["webapp_fi"][
            "restore_input_sha256"
        ],
    )
    target_map = json.loads(_canonical_json(TARGET_MAP))
    postgres_set = {
        target: {
            "source_role": row["source_role"],
            "artifact": dict(
                sources[row["source_role"]]["artifacts"]["database-backup"]
            ),
            "source_database": dict(
                sources[row["source_role"]]["source_database"]
            ),
        }
        for target, row in target_map.items()
    }
    file_set = {
        target: {
            "source_role": row["source_role"],
            "uploads-archive": dict(
                sources[row["source_role"]]["artifacts"][
                    "uploads-archive"
                ]
            ),
            "audit-archive": dict(
                sources[row["source_role"]]["artifacts"]["audit-archive"]
            ),
        }
        for target, row in target_map.items()
    }
    nginx_freeze = {
        "state": "legacy-frozen",
        "aggregate_sha256": receipt["aggregate_sha256"],
        "state_receipt_sha256": receipt_sha256,
        "global_generation_sha256": receipt["global_generation_sha256"],
        "role_generation_sha256": {
            role: receipt["readbacks"][role]["generation_sha256"]
            for role in NGINX.ROLE_ORDER
        },
        "role_bindings": dict(receipt["role_bindings"]),
        "journal_sha256": receipt["journal_sha256"],
        "journal_sequence": receipt["evidence_count"],
        "journal_tail_sha256": receipt["evidence_tail_sha256"],
        "external_readback_sha256": _sha256(
            _canonical_json(receipt["external_readback"])
        ),
    }
    snapshot_authorization_claim = {
        "claim_sha256": claim_sha256,
        "claim_epoch": claim["claim_epoch"],
        "previous_claim_sha256": claim["previous_claim_sha256"],
        "nonce": claim["nonce"],
        "owner_action": claim["owner_action"],
        "claim_document_status": claim["status"],
        "controller_lock_path_at_issue": claim["controller_lock_path"],
        "legacy_frozen_receipt_sha256": receipt_sha256,
        "receipt_journal_sha256": claim["receipt_journal_sha256"],
        "receipt_journal_sequence": claim["receipt_journal_sequence"],
        "receipt_journal_tail_sha256": claim[
            "receipt_journal_tail_sha256"
        ],
        "controller_journal_event_count": claim[
            "controller_journal_event_count"
        ],
        "claim_declared_controller_authoritative_at_issue": True,
        "copied_material_authoritative": False,
        "automatic_expiry_allowed": False,
        "reconciliation_required_after_crash": True,
        "claim_liveness_asserted": False,
        "future_install_or_restore_authority_implied": False,
        "fresh_live_authority_required_before_install_or_restore": True,
    }
    generation_basis = {
        "schema": "production-shadow-frozen-final-restore-generation-v1",
        "operation_id": controller["operation_id"],
        "release_sha": controller["release_sha"],
        "release_tree_sha": controller["release_tree_sha"],
        "controller_manifest_sha256": controller_sha256,
        "approval_sha256": approval_sha256,
        "target_map": target_map,
        "sources": sources,
        "nginx_freeze": nginx_freeze,
        "snapshot_authorization_claim": snapshot_authorization_claim,
        "webapp_ir_transport": ir_transport,
    }
    document = {
        "schema": SCHEMA,
        "status": "sealed",
        "campaign_id": controller["campaign_id"],
        "operation_id": controller["operation_id"],
        "release_sha": controller["release_sha"],
        "release_tree_sha": controller["release_tree_sha"],
        "legacy_release_sha": controller["legacy_release_sha"],
        "controller_manifest_sha256": controller_sha256,
        "approval_sha256": approval_sha256,
        "approval_policy_sha256": policy_sha256,
        "restore_generation_sha256": _sha256(
            _canonical_json(generation_basis)
        ),
        "target_map": target_map,
        "sources": sources,
        "postgres_snapshot_set_sha256": _sha256(
            _canonical_json(postgres_set)
        ),
        "reviewed_file_snapshot_set_sha256": _sha256(
            _canonical_json(file_set)
        ),
        "nginx_freeze": nginx_freeze,
        "snapshot_authorization_claim": snapshot_authorization_claim,
        "webapp_ir_transport": ir_transport,
        "constraints": {
            "plan_only_default": True,
            "network_io_performed": False,
            "object_storage_contacted": False,
            "production_contacted": False,
            "installer_executed": False,
            "restore_worker_executed": False,
            "service_mutated": False,
            "current_mutated": False,
            "container_mutated": False,
            "volume_mutated": False,
            "data_mutated": False,
            "legacy_redis_restore_included": False,
            "snapshot_authorization_claim_copy_is_not_live_authority": True,
            "snapshot_authorization_claim_liveness_asserted": False,
            "future_install_or_restore_authority_implied": False,
            "fresh_live_authority_required_before_install_or_restore": True,
        },
    }
    if (
        set(document) != RESTORE_SET_FIELDS
        or set(document["sources"]) != set(SOURCE_ROLES)
        or any(
            set(source) != SOURCE_RESTORE_FIELDS
            for source in document["sources"].values()
        )
        or set(document["nginx_freeze"]) != NGINX_FREEZE_FIELDS
        or set(document["snapshot_authorization_claim"])
        != SNAPSHOT_AUTHORIZATION_CLAIM_OUTPUT_FIELDS
        or set(document["webapp_ir_transport"])
        != IR_TRANSPORT_OUTPUT_FIELDS
        or set(document["constraints"]) != CONSTRAINT_FIELDS
    ):
        raise FrozenFinalRestoreSetError(
            "frozen-final restore-set fields are not exact"
        )
    return document


def confirmation_phrase(document: Mapping[str, Any]) -> str:
    return (
        "build-production-shadow-frozen-final-restore-set:"
        f"{document['operation_id']}:{document['release_sha']}:"
        f"{document['restore_generation_sha256']}"
    )


def restore_set_path(
    output_root: Path,
    document: Mapping[str, Any],
) -> tuple[Path, str]:
    payload = _canonical_json(document)
    digest = _sha256(payload)
    return (
        output_root
        / str(document["operation_id"])
        / "frozen-final-restore-sets"
        / digest
        / OUTPUT_FILENAME,
        digest,
    )


def _assert_private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise FrozenFinalRestoreSetError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise FrozenFinalRestoreSetError(
            f"{label} must be a real root-owned mode 0700 directory"
        )


def _ensure_private_child(parent: Path, name: str) -> Path:
    _assert_private_directory(parent, label="restore-set output parent")
    child = parent / name
    try:
        child.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise FrozenFinalRestoreSetError(
            "restore-set output namespace could not be created"
        ) from exc
    _assert_private_directory(child, label="restore-set output namespace")
    return child


def publish_restore_set(
    output_root: Path,
    document: Mapping[str, Any],
) -> tuple[str, Path, str]:
    if not output_root.is_absolute():
        raise FrozenFinalRestoreSetError(
            "restore-set output root must be absolute"
        )
    _assert_private_directory(output_root, label="restore-set output root")
    path, digest = restore_set_path(output_root, document)
    operation = _ensure_private_child(
        output_root,
        str(document["operation_id"]),
    )
    sets = _ensure_private_child(operation, "frozen-final-restore-sets")
    namespace = _ensure_private_child(sets, digest)
    if namespace / OUTPUT_FILENAME != path:
        raise FrozenFinalRestoreSetError(
            "restore-set digest namespace differs"
        )
    try:
        existing_names = set(os.listdir(namespace))
    except OSError as exc:
        raise FrozenFinalRestoreSetError(
            "restore-set digest namespace cannot be enumerated"
        ) from exc
    if existing_names - {OUTPUT_FILENAME}:
        raise FrozenFinalRestoreSetError(
            "restore-set digest namespace contains unexpected entries"
        )
    payload = _canonical_json(document)
    if path.exists() or path.is_symlink():
        try:
            observed = read_secure_bytes(
                path,
                label="existing frozen-final restore set",
                owner_uid=0,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise FrozenFinalRestoreSetError(
                "existing frozen-final restore set is unsafe"
            ) from exc
        if observed != payload:
            raise FrozenFinalRestoreSetError(
                "refusing to replace a different frozen-final restore set"
            )
        return "reused", path, digest
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="frozen-final restore set",
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
        observed = read_secure_bytes(
            path,
            label="frozen-final restore set",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise FrozenFinalRestoreSetError(
            "frozen-final restore set publication failed closed"
        ) from exc
    if observed != payload or _sha256(observed) != digest:
        raise FrozenFinalRestoreSetError(
            "frozen-final restore set readback differs"
        )
    try:
        final_names = set(os.listdir(namespace))
    except OSError as exc:
        raise FrozenFinalRestoreSetError(
            "restore-set digest namespace cannot be re-enumerated"
        ) from exc
    if final_names != {OUTPUT_FILENAME}:
        raise FrozenFinalRestoreSetError(
            "restore-set digest namespace is not exact after publication"
        )
    return "created", path, digest


def execute(
    *,
    output_root: Path,
    apply: bool,
    confirm: str | None,
    **inputs: Path,
) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise FrozenFinalRestoreSetError(
            "frozen-final restore-set builder must run as root"
        )
    document = build_restore_set(**inputs)
    output, restore_set_sha256 = restore_set_path(output_root, document)
    required = confirmation_phrase(document)
    base = {
        "schema": SCHEMA,
        "operation_id": document["operation_id"],
        "release_sha": document["release_sha"],
        "release_tree_sha": document["release_tree_sha"],
        "restore_generation_sha256": document[
            "restore_generation_sha256"
        ],
        "restore_set_sha256": restore_set_sha256,
        "output": os.fspath(output),
        "required_confirmation": required,
        "network_io": False,
        "object_storage_contacted": False,
        "production_contacted": False,
        "installer_executed": False,
        "restore_worker_executed": False,
        "runtime_mutated": False,
    }
    if not apply:
        if confirm is not None:
            raise FrozenFinalRestoreSetError(
                "--confirm is valid only with --apply"
            )
        return {
            **base,
            "status": "planned",
            "output_created": False,
        }
    if confirm != required:
        raise FrozenFinalRestoreSetError(
            f"apply requires --confirm {required}"
        )
    publication, path, digest = publish_restore_set(output_root, document)
    if path != output or digest != restore_set_sha256:
        raise FrozenFinalRestoreSetError(
            "published restore-set identity differs from the plan"
        )
    return {
        **base,
        "status": (
            "published" if publication == "created" else "already-published"
        ),
        "publication": publication,
        "output_created": publication == "created",
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--bot-fi-source-manifest", type=Path, required=True)
    parser.add_argument("--bot-fi-freeze-evidence", type=Path, required=True)
    parser.add_argument("--webapp-fi-source-manifest", type=Path, required=True)
    parser.add_argument("--webapp-fi-freeze-evidence", type=Path, required=True)
    parser.add_argument("--legacy-frozen-receipt", type=Path, required=True)
    parser.add_argument("--live-lease-claim", type=Path, required=True)
    parser.add_argument("--webapp-ir-transport", type=Path, required=True)
    parser.add_argument(
        "--webapp-ir-readback-receipt",
        type=Path,
        required=True,
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = execute(
            controller_manifest=args.controller_manifest,
            approval=args.approval,
            approval_policy=args.approval_policy,
            bot_fi_source_manifest=args.bot_fi_source_manifest,
            bot_fi_freeze_evidence=args.bot_fi_freeze_evidence,
            webapp_fi_source_manifest=args.webapp_fi_source_manifest,
            webapp_fi_freeze_evidence=args.webapp_fi_freeze_evidence,
            legacy_frozen_receipt=args.legacy_frozen_receipt,
            live_lease_claim=args.live_lease_claim,
            webapp_ir_transport=args.webapp_ir_transport,
            webapp_ir_readback_receipt=(
                args.webapp_ir_readback_receipt
            ),
            output_root=args.output_root,
            apply=args.apply,
            confirm=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except FrozenFinalRestoreSetError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "network_io": False,
                    "object_storage_contacted": False,
                    "production_contacted": False,
                    "runtime_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": "unexpected frozen-final restore-set failure",
                    "error_class": "UnexpectedFrozenFinalRestoreSetError",
                    "network_io": False,
                    "object_storage_contacted": False,
                    "production_contacted": False,
                    "runtime_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
