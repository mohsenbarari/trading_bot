from __future__ import annotations

import copy
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

from scripts import (
    orchestrate_production_shadow_frozen_final_restore as MODULE,
)
from scripts import production_shadow_frozen_final_restore_worker as WORKER


ZERO = "0" * 64
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
OPERATION_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
CAMPAIGN_ID = "2b195682-047c-4b84-a8cf-63bbc29f34b0"
RELEASE_SHA = "1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"
RELEASE_TREE_SHA = "2" * 40


def wa_version(
    version_id: str = "v-restore-001",
    *,
    object_key: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": "arvan-s3",
        "private": True,
        "versioned": True,
        "encryption": "age",
        "object_key": (
            object_key
            if object_key is not None
            else f"production/{version_id}/bundle.age"
        ),
        "version_id": version_id,
        "ciphertext_sha256": SHA_E,
        "readback_receipt_sha256": SHA_F,
        "exact_version_readback_verified": True,
        "payload_bytes_over_ssh": False,
        "presigned_url_persisted": False,
    }


def request_for(
    role: str,
    *,
    action: str = "apply",
) -> dict[str, Any]:
    paths = MODULE._expected_release_paths(OPERATION_ID, RELEASE_SHA)
    role_path = WORKER.ROLE_PATHS[role]
    root = MODULE.NGINX.CONTROLLER_SECRET_PREFIX
    incoming = root / OPERATION_ID / "frozen-final-inputs" / role_path
    claim_path = (
        root
        / OPERATION_ID
        / "nginx-coordinator"
        / "live-leases"
        / "claims"
        / f"{SHA_C}.json"
    )
    receipt_path = (
        root
        / OPERATION_ID
        / "nginx-coordinator"
        / "receipts"
        / f"legacy-frozen-{SHA_D}.json"
    )
    inputs = {
        "controller_manifest": str(incoming / "controller-manifest.json"),
        "restore_set": str(incoming / "restore-set.json"),
        "role_material": str(incoming / "role-material.tar"),
        "database_backup": str(incoming / "database.dump"),
        "uploads_archive": str(incoming / "uploads.tar.gz"),
        "audit_archive": str(incoming / "audit.tar.gz"),
        "canonical_compose": str(
            paths["release_root"]
            / "deploy/production/docker-compose.shadow.yml"
        ),
        "worker": str(paths["worker_path"]),
        "execution_envelope": str(incoming / "execution-envelope.json"),
        "fresh_live_lease_claim": str(claim_path),
        "legacy_frozen_receipt": str(receipt_path),
        "webapp_ir_transport_manifest": None,
        "webapp_ir_readback_receipt": None,
        "webapp_ir_control_transfer_receipt": None,
    }
    restore_version = None
    control_version = None
    if role == "webapp_ir":
        inputs.update(
            {
                "webapp_ir_transport_manifest": str(
                    incoming / "restore-transport-manifest.json"
                ),
                "webapp_ir_readback_receipt": str(
                    incoming / "restore-readback-receipt.json"
                ),
            }
        )
        restore_version = wa_version()
    authority = None
    if action == "apply":
        authority = {
            "claim_path": str(claim_path),
            "claim_sha256": SHA_C,
            "claim_epoch": 4,
            "claim_nonce": SHA_B,
            "legacy_frozen_receipt_path": str(receipt_path),
            "legacy_frozen_receipt_sha256": SHA_D,
        }
    request = {
        "schema": MODULE.HOST_REQUEST_SCHEMA,
        "action": action,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "controller_manifest_sha256": SHA_A,
        "restore_set_sha256": SHA_B,
        "restore_generation_sha256": SHA_C,
        "role": role,
        "expected_host": MODULE.ROLE_HOSTS[role],
        "expected_port": MODULE.ROLE_PORTS[role],
        "transport": MODULE.ROLE_TRANSPORTS[role],
        "release_root": str(paths["release_root"]),
        "agent_path": str(paths["agent_path"]),
        "agent_sha256": SHA_D,
        "installer_path": str(paths["installer_path"]),
        "installer_sha256": SHA_E,
        "worker_path": str(paths["worker_path"]),
        "worker_sha256": SHA_F,
        "inputs": inputs,
        "authority": authority,
        "wa_exact_version": restore_version,
        "wa_fresh_control_exact_version": control_version,
        "payload_bytes_over_control": False,
        "pull_policy": "never",
        "build_allowed": False,
        "app_services_allowed": False,
    }
    if role == "webapp_ir" and action == "apply":
        inputs["webapp_ir_control_transfer_receipt"] = str(
            incoming / "fresh-control-transfer-receipt.json"
        )
        basis = {
            "schema": (
                "production-shadow-frozen-final-wa-control-object-key-v1"
            ),
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "role": "webapp_ir",
            "claim_sha256": SHA_C,
            "claim_epoch": 4,
        }
        binding = hashlib.sha256(
            MODULE.canonical_json(basis)
        ).hexdigest()
        control_version = {
            **wa_version(
                "v-control-002",
                object_key=f"production/control/{binding}.age",
            ),
            "publication_mode": "create-if-absent",
            "object_key_binding_sha256": binding,
            "second_upload_performed": False,
        }
        request["wa_fresh_control_exact_version"] = control_version
    return request


class FakeControllerLease(MODULE.NGINX.CoordinatorLiveLease):
    def __init__(self, consumption_path: Path | None = None) -> None:
        root = MODULE.NGINX.CONTROLLER_SECRET_PREFIX
        coordinator = root / OPERATION_ID / "nginx-coordinator"
        self._claim_sha256 = SHA_C
        self._claim_path = (
            coordinator
            / "live-leases"
            / "claims"
            / f"{SHA_C}.json"
        )
        self._claim = {
            "schema": MODULE.NGINX.LIVE_LEASE_CLAIM_SCHEMA,
            "status": "active",
            "owner_action": WORKER.LIVE_LEASE_OWNER_ACTION,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "claim_epoch": 4,
            "nonce": SHA_B,
            "legacy_frozen_receipt_sha256": SHA_D,
            "controller_lock_path": str(coordinator / "coordinator.lock"),
            "controller_authoritative": True,
            "remote_copy_authoritative": False,
            "automatic_expiry_allowed": False,
            "reconciliation_required_after_crash": True,
        }
        self.verify_count = 0
        self.consume_calls: list[tuple[str, str]] = []
        self.fail_verify = False
        self._fake_consumption_path = consumption_path
        self._fake_consumed = False

    @property
    def claim_sha256(self) -> str:
        return self._claim_sha256

    @property
    def claim_path(self) -> Path:
        return self._claim_path

    @property
    def claim(self) -> dict[str, Any]:
        return copy.deepcopy(self._claim)

    @property
    def consumed(self) -> bool:
        return self._fake_consumed

    @property
    def consumption_path(self) -> Path | None:
        return self._fake_consumption_path

    def verify(self) -> Mapping[str, Any]:
        self.verify_count += 1
        if self.fail_verify:
            raise RuntimeError("stale or consumed")
        return {
            "phase": "legacy-frozen",
            "controller_lock_authority_observed": True,
        }

    def consume(self, *, outcome: str, outcome_sha256: str):
        self.consume_calls.append((outcome, outcome_sha256))
        self._fake_consumed = True
        if self._fake_consumption_path is None:
            return Path("/root/consumption.json"), SHA_E
        return self._fake_consumption_path, SHA_E


def controller_exchange(challenge: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **challenge,
        "schema": MODULE.RESPONSE_SCHEMA,
        "status": "controller-flock-verified",
        "challenge_sha256": hashlib.sha256(
            MODULE.canonical_json(challenge)
        ).hexdigest(),
        "response_nonce": hashlib.sha256(
            f"response-{challenge['sequence']}".encode("ascii")
        ).hexdigest(),
        "controller_lock_held": True,
        "controller_authoritative": True,
    }


def readback(
    path: str,
    document: Mapping[str, Any],
    *,
    newline: bool,
) -> dict[str, Any]:
    body = MODULE.canonical_json(document)
    payload = body + (b"\n" if newline else b"")
    return {
        "path": path,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "canonical_document_sha256": hashlib.sha256(body).hexdigest(),
        "newline_terminated": newline,
        "read_from_held_descriptor": True,
        "document": copy.deepcopy(document),
    }


def refresh_readback(row: dict[str, Any]) -> None:
    body = MODULE.canonical_json(row["document"])
    payload = body + (b"\n" if row["newline_terminated"] else b"")
    row["content_sha256"] = hashlib.sha256(payload).hexdigest()
    row["canonical_document_sha256"] = hashlib.sha256(body).hexdigest()
    row["bytes"] = len(payload)


def refresh_installation(installation: dict[str, Any]) -> None:
    unsigned = {
        key: value
        for key, value in installation.items()
        if key != "attestation_sha256"
    }
    installation["attestation_sha256"] = hashlib.sha256(
        MODULE.canonical_json(unsigned)
    ).hexdigest()


def transcript_for(
    request: Mapping[str, Any],
    *,
    completed_prefix: bool = False,
    exchange: Any = controller_exchange,
    nonce_salt: str = "stable",
) -> list[dict[str, Any]]:
    protocol = MODULE.HostAuthorityProtocol(
        request=request,
        exchange=exchange,
        nonce_factory=lambda: hashlib.sha256(
            (
                f"{request['role']}:{nonce_salt}:"
                f"{protocol.sequence + 1}"
            ).encode("ascii")
        ).hexdigest(),
    )
    claim = {
        "claim_epoch": 4,
        "nonce": SHA_B,
        "legacy_frozen_receipt_sha256": SHA_D,
    }
    protocol.verify(claim, "before-installation")
    protocol.verify(claim, "after-installation-readback")
    protocol.verify(claim, f"before:{request['role']}:journal-bootstrap")
    if completed_prefix:
        protocol.verify(
            claim,
            f"before:{request['role']}:completed-readback",
        )
        protocol.verify(
            claim,
            f"after:{request['role']}:completed-readback",
        )
    else:
        for action in WORKER.ACTIONS:
            protocol.verify(
                claim,
                f"before:{request['role']}:{action}",
            )
            protocol.verify(
                claim,
                f"after:{request['role']}:{action}",
            )
    protocol.verify(
        claim,
        f"after:{request['role']}:host-result-readback",
    )
    return protocol.transcript


def _verification_digest(entry: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        MODULE.canonical_json(entry["verification"])
    ).hexdigest()


def synthetic_host_result(
    request: Mapping[str, Any],
    *,
    completed_prefix: bool = False,
    exchange: Any = controller_exchange,
    nonce_salt: str = "stable",
    historical_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    role = request["role"]
    transcript = transcript_for(
        request,
        completed_prefix=completed_prefix,
        exchange=exchange,
        nonce_salt=nonce_salt,
    )
    by_boundary = {
        entry["challenge"]["boundary"]: entry for entry in transcript
    }
    if historical_result is None:
        receipt_document = {
            "schema": WORKER.INSTALLER_RECEIPT_SCHEMA,
            "status": "installed",
            "role": role,
        }
        receipt = readback(
            f"/root/{role}/installer-receipt.json",
            receipt_document,
            newline=False,
        )
        manifest_document = {
            "schema": WORKER.ROLE_MANIFEST_SCHEMA,
            "status": "installed",
            "role": role,
            "installer_receipt_sha256": receipt[
                "canonical_document_sha256"
            ],
            "restore_set_sha256": request["restore_set_sha256"],
            "restore_generation_sha256": request[
                "restore_generation_sha256"
            ],
        }
        manifest = readback(
            f"/root/{role}/restore-role-manifest.json",
            manifest_document,
            newline=False,
        )
        stable_transcript = (
            transcript
            if not completed_prefix
            else transcript_for(request, nonce_salt="stable-history")
        )
        stable_by_boundary = {
            entry["challenge"]["boundary"]: entry
            for entry in stable_transcript
        }
        evidence: dict[str, dict[str, Any]] = {}
        evidence_sha: dict[str, str] = {}
        for action in WORKER.ACTIONS:
            before = stable_by_boundary[
                f"before:{role}:{action}"
            ]
            after = stable_by_boundary[f"after:{role}:{action}"]
            semantic: dict[str, Any] = {
                "authority_before_sha256": _verification_digest(before),
                "authority_after_sha256": _verification_digest(after),
                "authority_before_sequence": before["verification"][
                    "verification_sequence"
                ],
                "authority_after_sequence": after["verification"][
                    "verification_sequence"
                ],
            }
            if action == "verify-final":
                semantic.update(
                    {
                        "database": {
                            "alembic_revision": "head",
                            "database_fingerprint_sha256": SHA_E,
                            "row_count": 10,
                            "table_count": 2,
                        },
                        "file_trees": {
                            "uploads": SHA_A,
                            "audit": SHA_F,
                        },
                        "redis_restore_bytes": 0,
                        "redis_pristine": True,
                    }
                )
            document = {
                "schema": WORKER.EVIDENCE_SCHEMA,
                "status": "completed",
                "action": action,
                "operation_id": OPERATION_ID,
                "role": role,
                "release_sha": RELEASE_SHA,
                "release_tree_sha": RELEASE_TREE_SHA,
                "controller_manifest_sha256": SHA_A,
                "restore_set_sha256": SHA_B,
                "restore_generation_sha256": SHA_C,
                "role_manifest_sha256": manifest[
                    "canonical_document_sha256"
                ],
                "installer_receipt_sha256": receipt[
                    "canonical_document_sha256"
                ],
                "legacy_frozen_receipt_sha256": SHA_D,
                "live_lease_claim_sha256": SHA_C,
                "live_lease_claim_epoch": 4,
                "live_lease_claim_nonce": SHA_B,
                "business_write_allowed": False,
                "public_or_private_app_started": False,
                "redis_restored": False,
                "current_mutated": False,
                "legacy_mutated": False,
                "object_storage_mutated": False,
                "semantic": semantic,
            }
            row = readback(
                f"/root/{role}/evidence/{action}.json",
                document,
                newline=True,
            )
            evidence[action] = row
            evidence_sha[action] = row["canonical_document_sha256"]
        events: list[dict[str, Any]] = []
        previous = ZERO
        for action in WORKER.ACTIONS:
            before = stable_by_boundary[f"before:{role}:{action}"]
            for kind in ("started", "completed"):
                event = {
                    "schema": WORKER.JOURNAL_EVENT_SCHEMA,
                    "operation_id": OPERATION_ID,
                    "role": role,
                    "release_sha": RELEASE_SHA,
                    "restore_set_sha256": SHA_B,
                    "restore_generation_sha256": SHA_C,
                    "role_manifest_sha256": manifest[
                        "canonical_document_sha256"
                    ],
                    "installer_receipt_sha256": receipt[
                        "canonical_document_sha256"
                    ],
                    "legacy_frozen_receipt_sha256": SHA_D,
                    "live_lease_claim_path": request["authority"][
                        "claim_path"
                    ],
                    "live_lease_claim_sha256": SHA_C,
                    "live_lease_claim_epoch": 4,
                    "live_lease_claim_nonce": SHA_B,
                    "index": len(events) + 1,
                    "kind": kind,
                    "action": action,
                    "attempt": 1,
                    "evidence_sha256": (
                        None if kind == "started" else evidence_sha[action]
                    ),
                    "authority_verification_sha256": (
                        _verification_digest(before)
                        if kind == "started"
                        else None
                    ),
                    "previous_event_sha256": previous,
                    "event_sha256": "",
                }
                event["event_sha256"] = WORKER._event_hash(event)
                events.append(event)
                previous = event["event_sha256"]
        restore_document = {
            "schema": WORKER.RESULT_SCHEMA,
            "status": "frozen-final-shadow-restored",
            "operation_id": OPERATION_ID,
            "role": role,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "controller_manifest_sha256": SHA_A,
            "installer_receipt_sha256": receipt[
                "canonical_document_sha256"
            ],
            "restore_set_sha256": SHA_B,
            "restore_generation_sha256": SHA_C,
            "source_role": (
                "bot_fi" if role == "bot_fi" else "webapp_fi"
            ),
            "live_lease_claim_sha256": SHA_C,
            "live_lease_claim_epoch": 4,
            "live_lease_claim_nonce": SHA_B,
            "legacy_frozen_receipt_sha256": SHA_D,
            "database": {
                "alembic_revision": "head",
                "database_fingerprint_sha256": SHA_E,
                "row_count": 10,
                "table_count": 2,
            },
            "file_trees": {"uploads": SHA_A, "audit": SHA_F},
            "redis_restore_bytes": 0,
            "redis_pristine": True,
            "public_or_private_app_started": False,
            "current_mutated": False,
            "legacy_mutated": False,
            "object_storage_mutated": False,
            "nginx_state": "legacy-frozen",
            "final_evidence_sha256": evidence_sha["verify-final"],
            "claim_consume_outcome": WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
            "aggregate_three_role_receipt_required": True,
            "claim_consumed_by_worker": False,
        }
        restore = readback(
            f"/root/{role}/evidence/restore-result.json",
            restore_document,
            newline=True,
        )
    else:
        receipt = copy.deepcopy(historical_result["installer_receipt"])
        manifest = copy.deepcopy(historical_result["role_manifest"])
        evidence = copy.deepcopy(historical_result["action_evidence"])
        evidence_sha = {
            action: evidence[action]["canonical_document_sha256"]
            for action in WORKER.ACTIONS
        }
        events = copy.deepcopy(historical_result["journal_events"])
        restore = copy.deepcopy(historical_result["restore_result"])
        restore_document = restore["document"]
    installation_events: list[dict[str, Any]] = []
    previous_installation = ZERO
    for index, entry in enumerate(transcript[:2], 1):
        body = {
            "schema": MODULE.INSTALLER.AUTHORITY_EVENT_SCHEMA,
            "index": index,
            "boundary": entry["challenge"]["boundary"],
            "verification": copy.deepcopy(entry["verification"]),
            "previous_event_sha256": previous_installation,
        }
        event = {
            **body,
            "event_sha256": hashlib.sha256(
                MODULE.canonical_json(body)
            ).hexdigest(),
        }
        installation_events.append(event)
        previous_installation = event["event_sha256"]
    installation_base = {
        "schema": MODULE.INSTALLER.INSTALLATION_ATTESTATION_SCHEMA,
        "status": "already-installed",
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "role": role,
        "source_role": restore_document["source_role"],
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "controller_manifest_sha256": SHA_A,
        "restore_set_sha256": SHA_B,
        "restore_generation_sha256": SHA_C,
        "role_manifest_sha256": manifest[
            "canonical_document_sha256"
        ],
        "installer_receipt_sha256": receipt[
            "canonical_document_sha256"
        ],
        "fresh_claim_sha256": SHA_C,
        "fresh_claim_epoch": 4,
        "fresh_claim_nonce": SHA_B,
        "legacy_frozen_receipt_sha256": SHA_D,
        "owner_action": WORKER.LIVE_LEASE_OWNER_ACTION,
        "intended_outcome": WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
        "authority_verifications": installation_events,
        "authority_verification_count": len(installation_events),
        "authority_verification_tail_sha256": previous_installation,
        "authority_transcript_sha256": hashlib.sha256(
            MODULE.canonical_json(installation_events)
        ).hexdigest(),
        "publications": {
            field: "reused"
            for field in MODULE.INSTALLATION_PUBLICATION_FIELDS
        },
        "worker_copied": False,
        "redis_restore_bytes": 0,
        "network_io_performed": False,
        "docker_invoked": False,
        "object_storage_contacted": False,
        "service_mutated": False,
        "current_mutated": False,
        "legacy_mutated": False,
    }
    installation = {
        **installation_base,
        "attestation_sha256": hashlib.sha256(
            MODULE.canonical_json(installation_base)
        ).hexdigest(),
    }
    bootstrap = by_boundary[f"before:{role}:journal-bootstrap"]
    completed_readback = None
    if completed_prefix:
        completed_readback = {
            "authority_before_sha256": _verification_digest(
                by_boundary[f"before:{role}:completed-readback"]
            ),
            "authority_after_sha256": _verification_digest(
                by_boundary[f"after:{role}:completed-readback"]
            ),
            "final_state_reverified": True,
        }
    worker_return = {
        "schema": WORKER.RESULT_SCHEMA,
        "operation_id": OPERATION_ID,
        "role": role,
        "release_sha": RELEASE_SHA,
        "restore_set_sha256": SHA_B,
        "restore_generation_sha256": SHA_C,
        "installer_receipt_sha256": receipt[
            "canonical_document_sha256"
        ],
        "live_lease_claim_sha256": SHA_C,
        "live_lease_claim_epoch": 4,
        "live_lease_claim_nonce": SHA_B,
        "legacy_frozen_receipt_sha256": SHA_D,
        "required_confirmation": (
            "restore-production-shadow-frozen-final:"
            f"{OPERATION_ID}:{role}:{SHA_C}:{SHA_C}:4"
        ),
        "plan_only_default": True,
        "static_claim_authoritative": False,
        "controller_live_verifier_required": True,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
        "status": "restored",
        "runtime_mutated": True,
        "completed_actions": list(WORKER.ACTIONS),
        "result": copy.deepcopy(restore_document),
        "result_sha256": restore["canonical_document_sha256"],
        "result_path": restore["path"],
        "action_evidence_sha256": evidence_sha,
        "result_publication": "reused",
        "bootstrap_authority_sha256": _verification_digest(bootstrap),
        "completed_readback": completed_readback,
        "claim_consumed": False,
        "aggregate_three_role_receipt_required": True,
    }
    transport: dict[str, Any] = {
        "mode": request["transport"],
        "fresh_control_exact_version": None,
        "fresh_control_transfer_receipt_sha256": None,
    }
    if role == "webapp_ir":
        transport.update(request["wa_exact_version"])
        transport["fresh_control_exact_version"] = request[
            "wa_fresh_control_exact_version"
        ]
        transport["fresh_control_transfer_receipt_sha256"] = SHA_A
    return {
        "schema": MODULE.HOST_RESULT_SCHEMA,
        "status": "restored-and-read-back",
        "operation_id": OPERATION_ID,
        "role": role,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "controller_manifest_sha256": SHA_A,
        "restore_set_sha256": SHA_B,
        "restore_generation_sha256": SHA_C,
        "source_role": restore_document["source_role"],
        "transport": transport,
        "installation_attestation": installation,
        "worker_return": worker_return,
        "role_manifest": manifest,
        "installer_receipt": receipt,
        "journal_prefix_event_count": (
            len(events) if completed_prefix else 0
        ),
        "journal_prefix_tail_sha256": (
            events[-1]["event_sha256"] if completed_prefix else ZERO
        ),
        "journal_prefix_completed_actions": (
            list(WORKER.ACTIONS) if completed_prefix else []
        ),
        "journal_prefix_active_action": None,
        "journal_events": events,
        "action_evidence": evidence,
        "restore_result": restore,
        "authority_transcript": transcript,
        "authority_transcript_count": len(transcript),
        "authority_transcript_sha256": hashlib.sha256(
            MODULE.canonical_json(transcript)
        ).hexdigest(),
        "authority_transcript_tail_sha256": transcript[-1][
            "entry_sha256"
        ],
        "observed_host_ipv4": [request["expected_host"]],
        "expected_host_verified": True,
        "payload_bytes_over_ssh": False,
        "presigned_url_persisted": False,
        "pull_performed": False,
        "build_performed": False,
        "app_services_started": False,
        "redis_restored": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
    }


class FakeProcess:
    def __init__(self, output: bytes) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(output)
        self.killed = False
        self.wait_calls = 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        return 0

    def kill(self) -> None:
        self.killed = True


def prepare_controller_output(
    requests: Mapping[str, Mapping[str, Any]],
) -> Path:
    output = MODULE.canonical_controller_output_directory(requests)
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def consumption_path_for(
    request: Mapping[str, Any],
) -> Path:
    authority = request["authority"]
    assert isinstance(authority, Mapping)
    return (
        Path(authority["claim_path"]).parent.parent
        / "consumptions"
        / f"{authority['claim_sha256']}.json"
    )


class FrozenFinalRestoreOrchestratorTests(unittest.TestCase):
    def test_plan_is_default_and_has_exact_three_roles(self) -> None:
        requests = {
            role: request_for(role, action="plan") for role in MODULE.ROLES
        }
        plan = MODULE.controller_plan(requests)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(set(plan["roles"]), set(MODULE.ROLES))
        self.assertTrue(plan["plan_only_default"])
        self.assertFalse(plan["app_services_allowed"])

    def test_request_rejects_payload_over_ssh(self) -> None:
        request = request_for("webapp_ir")
        request["inputs"]["database_backup"] = (
            "https://bucket.example/payload?X-Amz-Signature=secret"
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "path|payload",
        ):
            MODULE.validate_host_request(request)

    def test_request_rejects_wrong_or_missing_fresh_control_version(self) -> None:
        request = request_for("webapp_ir")
        request["wa_fresh_control_exact_version"]["version_id"] = ""
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "VersionId",
        ):
            MODULE.validate_host_request(request)
        request = request_for("webapp_ir")
        request["wa_fresh_control_exact_version"] = None
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "VersionId",
        ):
            MODULE.validate_host_request(request)

    def test_protocol_uses_unpredictable_ordered_challenges(self) -> None:
        request = request_for("webapp_fi")
        counter = iter((SHA_A, SHA_E))
        protocol = MODULE.HostAuthorityProtocol(
            request=request,
            exchange=controller_exchange,
            nonce_factory=lambda: next(counter),
        )
        claim = {
            "claim_epoch": 4,
            "nonce": SHA_B,
            "legacy_frozen_receipt_sha256": SHA_D,
        }
        first = protocol.verify(claim, "before:webapp_fi:restore")
        second = protocol.verify(claim, "after:webapp_fi:restore")
        self.assertEqual(first["verification_sequence"], 1)
        self.assertEqual(second["verification_sequence"], 2)
        self.assertNotEqual(
            protocol.transcript[0]["challenge"]["challenge_nonce"],
            protocol.transcript[1]["challenge"]["challenge_nonce"],
        )
        self.assertEqual(
            protocol.transcript[1]["previous_entry_sha256"],
            protocol.transcript[0]["entry_sha256"],
        )

    def test_transcript_tampering_is_rejected(self) -> None:
        request = request_for("bot_fi")
        transcript = transcript_for(request)
        transcript[1]["challenge"]["boundary"] = "tampered"
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "chain|challenge",
        ):
            MODULE.validate_authority_transcript(
                transcript,
                request=request,
            )

    def test_stale_or_consumed_claim_cannot_answer_challenge(self) -> None:
        request = request_for("bot_fi")
        protocol = MODULE.HostAuthorityProtocol(
            request=request,
            exchange=controller_exchange,
            nonce_factory=lambda: SHA_A,
        )
        claim = {
            "claim_epoch": 4,
            "nonce": SHA_B,
            "legacy_frozen_receipt_sha256": SHA_D,
        }
        challenge: dict[str, Any] = {}

        def capture(value: Mapping[str, Any]) -> Mapping[str, Any]:
            challenge.update(value)
            return controller_exchange(value)

        protocol.exchange = capture
        protocol.verify(claim, "before:bot_fi:restore")
        lease = FakeControllerLease()
        lease.fail_verify = True
        with self.assertRaises(RuntimeError):
            MODULE.controller_authority_response(
                challenge,
                lease=lease,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                role="bot_fi",
                expected_previous=ZERO,
                expected_sequence=1,
            )

    def test_remote_eof_fails_closed(self) -> None:
        request = request_for("bot_fi")
        process = FakeProcess(b"")
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "EOF",
        ):
            MODULE.run_interactive_host(
                request,
                lease=FakeControllerLease(),
                ssh_identity=Path("/root/.ssh/id_ed25519"),
                known_hosts=Path("/root/.ssh/known_hosts"),
                session_factory=lambda _arguments: process,
            )
        self.assertTrue(process.killed)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)

    def test_commands_disable_bytecode_and_never_carry_request_payload(self) -> None:
        local = MODULE.session_arguments(
            request_for("bot_fi"),
            ssh_identity=Path("/root/.ssh/id_ed25519"),
            known_hosts=Path("/root/.ssh/known_hosts"),
        )
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", local)
        self.assertIn("-B", local)
        self.assertNotIn(MODULE.encode_host_request(request_for("bot_fi")), local)
        remote = MODULE.session_arguments(
            request_for("webapp_ir"),
            ssh_identity=Path("/root/.ssh/id_ed25519"),
            known_hosts=Path("/root/.ssh/known_hosts"),
        )
        self.assertEqual(remote[0], MODULE.SSH)
        self.assertIn("StrictHostKeyChecking=yes", remote)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", remote[-1])
        self.assertNotIn("https://", " ".join(remote))

    def test_actual_result_byte_tampering_is_rejected(self) -> None:
        request = request_for("webapp_fi")
        result = synthetic_host_result(request)
        MODULE.validate_host_result(result, request=request)
        result["restore_result"]["document"]["database"]["row_count"] += 1
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "actual-byte",
        ):
            MODULE.validate_host_result(result, request=request)

    def test_wrong_wa_version_in_result_is_rejected(self) -> None:
        request = request_for("webapp_ir")
        result = synthetic_host_result(request)
        MODULE.validate_host_result(result, request=request)
        result["transport"]["version_id"] = "wrong-version"
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "VersionId",
        ):
            MODULE.validate_host_result(result, request=request)

    def test_installation_attestation_is_an_exact_transcript_prefix(
        self,
    ) -> None:
        request = request_for("bot_fi")
        baseline = synthetic_host_result(request)
        MODULE.validate_host_result(baseline, request=request)

        extra = copy.deepcopy(baseline)
        extra["installation_attestation"]["unexpected"] = False
        refresh_installation(extra["installation_attestation"])
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "fields",
        ):
            MODULE.validate_host_result(extra, request=request)

        wrong_count = copy.deepcopy(baseline)
        wrong_count["installation_attestation"][
            "authority_verification_count"
        ] += 1
        refresh_installation(wrong_count["installation_attestation"])
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "count",
        ):
            MODULE.validate_host_result(wrong_count, request=request)

        replayed = copy.deepcopy(baseline)
        installation = replayed["installation_attestation"]
        events = installation["authority_verifications"]
        events[0]["boundary"] = events[1]["boundary"]
        events[0]["verification"] = copy.deepcopy(
            events[1]["verification"]
        )
        previous = ZERO
        for index, event in enumerate(events, 1):
            event["index"] = index
            event["previous_event_sha256"] = previous
            body = {
                key: value
                for key, value in event.items()
                if key != "event_sha256"
            }
            event["event_sha256"] = hashlib.sha256(
                MODULE.canonical_json(body)
            ).hexdigest()
            previous = event["event_sha256"]
        installation["authority_verification_tail_sha256"] = previous
        installation["authority_transcript_sha256"] = hashlib.sha256(
            MODULE.canonical_json(events)
        ).hexdigest()
        refresh_installation(installation)
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "prefix|differs",
        ):
            MODULE.validate_host_result(replayed, request=request)

    def test_worker_journal_requires_every_completed_action(self) -> None:
        request = request_for("webapp_fi")
        result = synthetic_host_result(request)
        result["journal_events"].pop()
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "every action",
        ):
            MODULE.validate_host_result(result, request=request)

    def test_wrong_host_identity_stops_before_installer(self) -> None:
        request = request_for("bot_fi")
        installer = mock.Mock()
        with (
            mock.patch.object(os, "geteuid", return_value=0),
            mock.patch.object(os, "getegid", return_value=0),
            mock.patch.dict(
                os.environ,
                {"PYTHONDONTWRITEBYTECODE": "1"},
            ),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreOrchestratorError,
                "IPv4 identity",
            ),
        ):
            MODULE.execute_host_request(
                request,
                installer_module=installer,
                observed_host_addresses={"192.0.2.77"},
            )
        installer.preflight_installation.assert_not_called()
        installer.execute_installation.assert_not_called()

    def test_slowloris_line_without_newline_hits_deadline(self) -> None:
        read_fd, write_fd = os.pipe()
        stream = os.fdopen(read_fd, "rb", buffering=0)
        try:
            os.write(write_fd, b"{")
            reader = MODULE.DeadlineLineReader(stream)
            with self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreOrchestratorError,
                "timed out",
            ):
                reader.read_line(maximum=1024, timeout=0.02)
        finally:
            os.close(write_fd)
            stream.close()

    def test_trailing_host_frame_is_rejected(self) -> None:
        request = request_for("bot_fi")
        first = MODULE.canonical_json(
            {"schema": MODULE.HOST_RESULT_SCHEMA}
        )
        process = FakeProcess(first + b"\n{}\n")
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "trailing",
        ):
            MODULE.run_interactive_host(
                request,
                lease=FakeControllerLease(),
                ssh_identity=Path("/root/.ssh/id_ed25519"),
                known_hosts=Path("/root/.ssh/known_hosts"),
                session_factory=lambda _arguments: process,
            )
        self.assertTrue(process.killed)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)

    def test_controller_session_rejects_out_of_order_challenge(self) -> None:
        request = request_for("bot_fi")
        session = MODULE.ControllerAuthoritySession(
            lease=FakeControllerLease(),
            request=request,
        )
        challenge = transcript_for(request)[1]["challenge"]
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreOrchestratorError,
            "challenge|sequence|chain",
        ):
            session.respond(challenge)

    def test_generic_invoker_must_use_current_authority_callback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.NGINX,
            "CONTROLLER_SECRET_PREFIX",
            Path(directory) / "controller",
        ):
            plans = {
                role: request_for(role, action="plan")
                for role in MODULE.ROLES
            }
            output = prepare_controller_output(plans)
            invoke_calls: list[str] = []

            def invoke(
                request: Mapping[str, Any],
                _exchange: Any,
            ) -> Mapping[str, Any]:
                invoke_calls.append(request["role"])
                return synthetic_host_result(request)

            with self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreOrchestratorError,
                "current controller authority session",
            ):
                MODULE.run_three_roles_under_lease(
                    lease=FakeControllerLease(),
                    requests=plans,
                    prepare_request=lambda role, *_args: request_for(role),
                    invoke=invoke,
                    output_directory=output,
                    consumption_readback=lambda *_args: (
                        Path("/root/not-used"),
                        SHA_E,
                        {},
                    ),
                )
        self.assertEqual(invoke_calls, ["bot_fi"])

    def test_noncanonical_output_root_causes_zero_writes(self) -> None:
        plans = {
            role: request_for(role, action="plan")
            for role in MODULE.ROLES
        }
        prepare = mock.Mock()
        invoke = mock.Mock()
        with (
            mock.patch.object(
                MODULE,
                "_ensure_private_directory",
            ) as ensure,
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreOrchestratorError,
                "campaign-scoped",
            ),
        ):
            MODULE.run_three_roles_under_lease(
                lease=FakeControllerLease(),
                requests=plans,
                prepare_request=prepare,
                invoke=invoke,
                output_directory=Path(
                    "/srv/trading-bot/current/frozen-final-test"
                ),
                consumption_readback=lambda *_args: (
                    Path("/root/not-used"),
                    SHA_E,
                    {},
                ),
            )
        ensure.assert_not_called()
        prepare.assert_not_called()
        invoke.assert_not_called()

    def test_apply_rejects_wrong_lease_type_or_owner_before_writes(
        self,
    ) -> None:
        for lease in (object(), FakeControllerLease()):
            with self.subTest(lease_type=type(lease).__name__), (
                tempfile.TemporaryDirectory()
            ) as directory, mock.patch.object(
                MODULE.NGINX,
                "CONTROLLER_SECRET_PREFIX",
                Path(directory) / "controller",
            ):
                plans = {
                    role: request_for(role, action="plan")
                    for role in MODULE.ROLES
                }
                output = prepare_controller_output(plans)
                if isinstance(lease, FakeControllerLease):
                    lease = FakeControllerLease()
                    lease._claim["owner_action"] = "handoff-shadow-readonly"
                with (
                    mock.patch.object(
                        MODULE,
                        "_ensure_private_directory",
                    ) as ensure,
                    self.assertRaisesRegex(
                        MODULE.FrozenFinalRestoreOrchestratorError,
                        "CoordinatorLiveLease|owner",
                    ),
                ):
                    MODULE.run_three_roles_under_lease(
                        lease=lease,
                        requests=plans,
                        prepare_request=mock.Mock(),
                        invoke=mock.Mock(),
                        output_directory=output,
                        consumption_readback=lambda *_args: (
                            Path("/root/not-used"),
                            SHA_E,
                            {},
                        ),
                    )
                ensure.assert_not_called()

    def test_host_result_can_exceed_control_frame_but_not_result_bound(
        self,
    ) -> None:
        request = request_for("bot_fi")
        result = synthetic_host_result(request)
        evidence = result["action_evidence"]["verify-inputs"]
        evidence["document"]["semantic"]["padding"] = "x" * (
            MODULE.MAX_CONTROL_BYTES + 1024
        )
        refresh_readback(evidence)
        evidence_sha256 = evidence["canonical_document_sha256"]
        result["worker_return"]["action_evidence_sha256"][
            "verify-inputs"
        ] = evidence_sha256
        previous = ZERO
        for event in result["journal_events"]:
            if (
                event["kind"] == "completed"
                and event["action"] == "verify-inputs"
            ):
                event["evidence_sha256"] = evidence_sha256
            event["previous_event_sha256"] = previous
            event["event_sha256"] = WORKER._event_hash(event)
            previous = event["event_sha256"]
        payload = MODULE.canonical_json(result)
        self.assertGreater(len(payload), MODULE.MAX_CONTROL_BYTES)
        self.assertLess(len(payload), MODULE.MAX_HOST_RESULT_BYTES)
        MODULE.validate_host_result(result, request=request)

        output = b"".join(
            MODULE.canonical_json(entry["challenge"]) + b"\n"
            for entry in result["authority_transcript"]
        ) + payload + b"\n"
        process = FakeProcess(output)
        response_nonces = [
            entry["response"]["response_nonce"]
            for entry in result["authority_transcript"]
        ]
        with mock.patch.object(
            MODULE.secrets,
            "token_hex",
            side_effect=response_nonces,
        ):
            observed = MODULE.run_interactive_host(
                request,
                lease=FakeControllerLease(),
                ssh_identity=Path("/root/.ssh/id_ed25519"),
                known_hosts=Path("/root/.ssh/known_hosts"),
                session_factory=lambda _arguments: process,
            )
        self.assertEqual(
            observed["restore_result"],
            result["restore_result"],
        )

    def test_payload_preparation_failure_never_invokes_a_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.NGINX,
            "CONTROLLER_SECRET_PREFIX",
            Path(directory) / "controller",
        ):
            plans = {
                role: request_for(role, action="plan")
                for role in MODULE.ROLES
            }
            output = prepare_controller_output(plans)
            invoked: list[str] = []

            def prepare(
                role: str,
                _request: Mapping[str, Any],
                _lease: Any,
            ) -> Mapping[str, Any]:
                if role == "webapp_ir":
                    raise RuntimeError("Arvan exact-VersionId failed")
                return request_for(role)

            with self.assertRaisesRegex(RuntimeError, "VersionId"):
                MODULE.run_three_roles_under_lease(
                    lease=FakeControllerLease(),
                    requests=plans,
                    prepare_request=prepare,
                    invoke=lambda request, _exchange: invoked.append(
                        request["role"]
                    ),
                    output_directory=output,
                    consumption_readback=lambda *_args: (
                        Path("/root/not-used"),
                        SHA_E,
                        {},
                    ),
                )
        self.assertEqual(invoked, [])

    def test_crash_before_consume_resumes_without_reinvoking_roles(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.NGINX,
            "CONTROLLER_SECRET_PREFIX",
            Path(directory) / "controller",
        ):
            plans = {
                role: request_for(role, action="plan")
                for role in MODULE.ROLES
            }
            output = prepare_controller_output(plans)
            consumption_path = consumption_path_for(
                request_for("bot_fi")
            )
            lease = FakeControllerLease(consumption_path)
            invoked: list[str] = []

            def invoke(
                request: Mapping[str, Any],
                exchange: Any,
            ) -> Mapping[str, Any]:
                invoked.append(request["role"])
                return synthetic_host_result(
                    request,
                    exchange=exchange,
                    nonce_salt=f"first-{request['role']}",
                )

            def crash(name: str) -> None:
                if name == "after-completion-before-consume":
                    raise RuntimeError("crash before consume")

            with self.assertRaisesRegex(RuntimeError, "before consume"):
                MODULE.run_three_roles_under_lease(
                    lease=lease,
                    requests=plans,
                    prepare_request=lambda role, *_args: request_for(role),
                    invoke=invoke,
                    output_directory=output,
                    consumption_readback=lambda *_args: (
                        consumption_path,
                        SHA_E,
                        {},
                    ),
                    checkpoint=crash,
                )
            self.assertEqual(lease.consume_calls, [])

            def readback(
                claimed_path: Path | None,
                _claimed_sha256: str | None,
                _completion: Mapping[str, Any],
                _completion_sha256: str,
            ):
                if claimed_path is None:
                    raise MODULE.ConsumptionAuditAbsent("not consumed")
                return consumption_path, SHA_E, {"status": "consumed"}

            outcome = MODULE.run_three_roles_under_lease(
                lease=lease,
                requests=plans,
                prepare_request=lambda *_args: self.fail(
                    "prepared request should be reused"
                ),
                invoke=lambda *_args: self.fail(
                    "completed roles must not be reinvoked"
                ),
                output_directory=output,
                consumption_readback=readback,
            )
            self.assertEqual(outcome["status"], "complete")
            self.assertEqual(len(lease.consume_calls), 1)
            self.assertEqual(invoked, list(MODULE.ROLES))

    def test_crash_after_consume_recovers_without_second_consume(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.NGINX,
            "CONTROLLER_SECRET_PREFIX",
            Path(directory) / "controller",
        ):
            plans = {
                role: request_for(role, action="plan")
                for role in MODULE.ROLES
            }
            apply_requests = {
                role: request_for(role) for role in MODULE.ROLES
            }
            output = prepare_controller_output(plans)
            consumption_path = consumption_path_for(
                apply_requests["bot_fi"]
            )
            lease = FakeControllerLease(consumption_path)

            def invoke(
                request: Mapping[str, Any],
                exchange: Any,
            ) -> Mapping[str, Any]:
                return synthetic_host_result(
                    request,
                    exchange=exchange,
                    nonce_salt=request["role"],
                )

            def crash(name: str) -> None:
                if name == "after-consume-before-receipt":
                    raise RuntimeError("crash after consume")

            with self.assertRaisesRegex(RuntimeError, "after consume"):
                MODULE.run_three_roles_under_lease(
                    lease=lease,
                    requests=plans,
                    prepare_request=lambda role, *_args: apply_requests[
                        role
                    ],
                    invoke=invoke,
                    output_directory=output,
                    consumption_readback=lambda *_args: self.fail(
                        "crash must precede readback"
                    ),
                    checkpoint=crash,
                )
            self.assertEqual(len(lease.consume_calls), 1)
            journal = MODULE.ControllerJournalStore(output, apply_requests)
            self.assertIsNotNone(journal.document["completion"])
            self.assertIsNone(journal.document["consumption"])

            class FakeCoordinatorLock:
                def __init__(self, root: Path) -> None:
                    self.root = root

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            inputs = mock.Mock(
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                release_tree_sha=RELEASE_TREE_SHA,
                coordinator_root=output.parent,
            )
            with (
                mock.patch.object(
                    MODULE.NGINX,
                    "_CoordinatorLock",
                    FakeCoordinatorLock,
                ),
                mock.patch.object(
                    MODULE,
                    "coordinator_consumption_readback",
                    return_value=(
                        consumption_path,
                        SHA_E,
                        {"status": "consumed"},
                    ),
                ),
            ):
                recovered = MODULE.recover_consumed_controller_operation(
                    inputs=inputs,
                    output_directory=output,
                    requests=plans,
                )
            self.assertEqual(
                recovered["status"],
                "complete-recovered-after-consume",
            )
            self.assertFalse(recovered["second_consume_performed"])
            self.assertEqual(len(lease.consume_calls), 1)

    def test_malformed_consumption_audit_never_falls_through_to_consume(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.NGINX,
            "CONTROLLER_SECRET_PREFIX",
            Path(directory) / "controller",
        ):
            plans = {
                role: request_for(role, action="plan")
                for role in MODULE.ROLES
            }
            output = prepare_controller_output(plans)
            lease = FakeControllerLease(
                consumption_path_for(request_for("bot_fi"))
            )

            def crash(name: str) -> None:
                if name == "after-completion-before-consume":
                    raise RuntimeError("stop")

            with self.assertRaisesRegex(RuntimeError, "stop"):
                MODULE.run_three_roles_under_lease(
                    lease=lease,
                    requests=plans,
                    prepare_request=lambda role, *_args: request_for(role),
                    invoke=lambda request, exchange: synthetic_host_result(
                        request,
                        exchange=exchange,
                    ),
                    output_directory=output,
                    consumption_readback=lambda *_args: (
                        Path("/root/not-used"),
                        SHA_E,
                        {},
                    ),
                    checkpoint=crash,
                )
            with self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreOrchestratorError,
                "malformed audit",
            ):
                MODULE.run_three_roles_under_lease(
                    lease=lease,
                    requests=plans,
                    prepare_request=lambda *_args: self.fail(
                        "prepared requests must be reused"
                    ),
                    invoke=lambda *_args: self.fail(
                        "roles must not be reinvoked"
                    ),
                    output_directory=output,
                    consumption_readback=lambda *_args: (
                        _ for _ in ()
                    ).throw(
                        MODULE.FrozenFinalRestoreOrchestratorError(
                            "malformed audit"
                        )
                    ),
                )
            self.assertEqual(lease.consume_calls, [])

    def test_completion_binds_exact_three_role_actual_documents(self) -> None:
        requests = {
            role: request_for(role) for role in MODULE.ROLES
        }
        results = {
            role: synthetic_host_result(requests[role])
            for role in MODULE.ROLES
        }
        completion, digest = MODULE.build_completion(requests, results)
        self.assertEqual(set(completion["roles"]), set(MODULE.ROLES))
        self.assertEqual(
            digest,
            hashlib.sha256(
                MODULE.canonical_json(completion)
            ).hexdigest(),
        )
        self.assertFalse(completion["claim_consumed"])
        self.assertFalse(completion["consumption_receipt_included"])

    def test_consume_happens_only_after_persisted_three_role_completion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.NGINX,
            "CONTROLLER_SECRET_PREFIX",
            Path(directory) / "controller",
        ):
            requests = {
                role: request_for(role) for role in MODULE.ROLES
            }
            results = {
                role: synthetic_host_result(requests[role])
                for role in MODULE.ROLES
            }
            root = MODULE.canonical_controller_output_directory(requests)
            root.parent.mkdir(parents=True)
            journal = MODULE.ControllerJournalStore(root, requests)
            for role in MODULE.ROLES:
                journal.record_role(role, results[role])
            consumption_path = (
                Path(requests["bot_fi"]["authority"]["claim_path"])
                .parent.parent
                / "consumptions"
                / f"{SHA_C}.json"
            )
            lease = FakeControllerLease(consumption_path)
            checkpoints: list[str] = []

            def consumption_readback(
                claimed_path: Path | None,
                claimed_sha256: str | None,
                _completion: Mapping[str, Any],
                _completion_sha256: str,
            ):
                self.assertEqual(claimed_path, consumption_path)
                self.assertEqual(claimed_sha256, SHA_E)
                return consumption_path, SHA_E, {"status": "consumed"}

            outcome = MODULE.consume_after_completion(
                lease=lease,
                requests=requests,
                results=results,
                output_directory=root,
                journal=journal,
                consumption_readback=consumption_readback,
                checkpoint=checkpoints.append,
            )
            self.assertTrue(Path(outcome["completion_path"]).is_file())
            self.assertEqual(
                checkpoints[0],
                "after-completion-before-consume",
            )
            self.assertEqual(len(lease.consume_calls), 1)
            self.assertEqual(
                lease.consume_calls[0],
                (
                    WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
                    outcome["completion_sha256"],
                ),
            )
            self.assertNotEqual(
                outcome["completion_sha256"],
                outcome["post_consumption_receipt_sha256"],
            )

    def test_missing_role_never_consumes_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.NGINX,
            "CONTROLLER_SECRET_PREFIX",
            Path(directory) / "controller",
        ):
            requests = {
                role: request_for(role) for role in MODULE.ROLES
            }
            results = {
                role: synthetic_host_result(requests[role])
                for role in MODULE.ROLES[:-1]
            }
            root = MODULE.canonical_controller_output_directory(requests)
            root.parent.mkdir(parents=True)
            journal = MODULE.ControllerJournalStore(root, requests)
            consumption_path = (
                Path(requests["bot_fi"]["authority"]["claim_path"])
                .parent.parent
                / "consumptions"
                / f"{SHA_C}.json"
            )
            lease = FakeControllerLease(consumption_path)
            with self.assertRaisesRegex(
                MODULE.FrozenFinalRestoreOrchestratorError,
                "exactly three",
            ):
                MODULE.consume_after_completion(
                    lease=lease,
                    requests=requests,
                    results=results,
                    output_directory=root,
                    journal=journal,
                    consumption_readback=lambda *_args: (
                        consumption_path,
                        SHA_E,
                        {},
                    ),
                )
        self.assertEqual(lease.consume_calls, [])

    def test_partial_resume_revalidates_every_role_and_rejects_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.NGINX,
            "CONTROLLER_SECRET_PREFIX",
            Path(directory) / "controller",
        ):
            plans = {
                role: request_for(role, action="plan")
                for role in MODULE.ROLES
            }
            output = MODULE.canonical_controller_output_directory(plans)
            output.parent.mkdir(parents=True)
            consumption_path = (
                MODULE.NGINX.CONTROLLER_SECRET_PREFIX
                / OPERATION_ID
                / "nginx-coordinator"
                / "live-leases"
                / "consumptions"
                / f"{SHA_C}.json"
            )
            lease = FakeControllerLease(consumption_path)
            history: dict[str, Mapping[str, Any]] = {}
            calls: list[str] = []

            def prepare(
                role: str,
                _template: Mapping[str, Any],
                _lease: Any,
            ) -> Mapping[str, Any]:
                return request_for(role)

            def first_invoke(
                request: Mapping[str, Any],
                exchange: Any,
            ) -> Mapping[str, Any]:
                calls.append(request["role"])
                result = synthetic_host_result(
                    request,
                    exchange=exchange,
                    nonce_salt="first",
                )
                history[request["role"]] = result
                return result

            def crash(name: str) -> None:
                if name == "after-role:bot_fi":
                    raise RuntimeError("simulated crash")

            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                MODULE.run_three_roles_under_lease(
                    lease=lease,
                    requests=plans,
                    prepare_request=prepare,
                    invoke=first_invoke,
                    output_directory=output,
                    consumption_readback=lambda *_args: (
                        consumption_path,
                        SHA_E,
                        {},
                    ),
                    checkpoint=crash,
                )

            def resumed_invoke(
                request: Mapping[str, Any],
                exchange: Any,
            ) -> Mapping[str, Any]:
                calls.append(request["role"])
                result = synthetic_host_result(
                    request,
                    completed_prefix=True,
                    exchange=exchange,
                    nonce_salt="resume",
                    historical_result=history[request["role"]],
                )
                result["transport"]["resume_observation"] = "drift"
                return result

            with self.assertRaises(
                MODULE.FrozenFinalRestoreOrchestratorError
            ):
                MODULE.run_three_roles_under_lease(
                    lease=lease,
                    requests=plans,
                    prepare_request=prepare,
                    invoke=resumed_invoke,
                    output_directory=output,
                    consumption_readback=lambda *_args: (
                        consumption_path,
                        SHA_E,
                        {},
                    ),
                )
        self.assertEqual(calls, ["bot_fi", "bot_fi"])
        self.assertEqual(lease.consume_calls, [])

    def test_partial_resume_accepts_fresh_transcript_with_stable_closure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.NGINX,
            "CONTROLLER_SECRET_PREFIX",
            Path(directory) / "controller",
        ):
            plans = {
                role: request_for(role, action="plan")
                for role in MODULE.ROLES
            }
            output = prepare_controller_output(plans)
            apply_requests = {
                role: request_for(role) for role in MODULE.ROLES
            }
            consumption_path = consumption_path_for(
                apply_requests["bot_fi"]
            )
            lease = FakeControllerLease(consumption_path)
            history: dict[str, Mapping[str, Any]] = {}
            calls: list[str] = []
            prepare_calls: list[str] = []

            def prepare(
                role: str,
                _template: Mapping[str, Any],
                _lease: Any,
            ) -> Mapping[str, Any]:
                prepare_calls.append(role)
                return apply_requests[role]

            def first_invoke(
                request: Mapping[str, Any],
                exchange: Any,
            ) -> Mapping[str, Any]:
                calls.append(request["role"])
                result = synthetic_host_result(
                    request,
                    exchange=exchange,
                    nonce_salt="initial",
                )
                history[request["role"]] = result
                return result

            def crash(name: str) -> None:
                if name == "after-role:bot_fi":
                    raise RuntimeError("role crash")

            with self.assertRaisesRegex(RuntimeError, "role crash"):
                MODULE.run_three_roles_under_lease(
                    lease=lease,
                    requests=plans,
                    prepare_request=prepare,
                    invoke=first_invoke,
                    output_directory=output,
                    consumption_readback=lambda *_args: (
                        consumption_path,
                        SHA_E,
                        {},
                    ),
                    checkpoint=crash,
                )

            def resumed_invoke(
                request: Mapping[str, Any],
                exchange: Any,
            ) -> Mapping[str, Any]:
                calls.append(request["role"])
                if request["role"] == "bot_fi":
                    return synthetic_host_result(
                        request,
                        completed_prefix=True,
                        exchange=exchange,
                        nonce_salt="fresh-resume",
                        historical_result=history["bot_fi"],
                    )
                return synthetic_host_result(
                    request,
                    exchange=exchange,
                    nonce_salt=f"resume-{request['role']}",
                )

            outcome = MODULE.run_three_roles_under_lease(
                lease=lease,
                requests=plans,
                prepare_request=prepare,
                invoke=resumed_invoke,
                output_directory=output,
                consumption_readback=lambda *_args: (
                    consumption_path,
                    SHA_E,
                    {"status": "consumed"},
                ),
            )
            self.assertEqual(outcome["status"], "complete")
            self.assertEqual(
                calls,
                ["bot_fi", "bot_fi", "webapp_fi", "webapp_ir"],
            )
            self.assertEqual(prepare_calls, list(MODULE.ROLES))
            self.assertEqual(len(lease.consume_calls), 1)

    def test_recovery_rejects_tampered_completion_and_role_observation(
        self,
    ) -> None:
        for target in ("completion", "role"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as (
                directory
            ), mock.patch.object(
                MODULE.NGINX,
                "CONTROLLER_SECRET_PREFIX",
                Path(directory) / "controller",
            ):
                plans = {
                    role: request_for(role, action="plan")
                    for role in MODULE.ROLES
                }
                apply_requests = {
                    role: request_for(role) for role in MODULE.ROLES
                }
                output = prepare_controller_output(plans)
                lease = FakeControllerLease(
                    consumption_path_for(apply_requests["bot_fi"])
                )

                def crash(name: str) -> None:
                    if name == "after-completion-before-consume":
                        raise RuntimeError("completion crash")

                with self.assertRaisesRegex(
                    RuntimeError,
                    "completion crash",
                ):
                    MODULE.run_three_roles_under_lease(
                        lease=lease,
                        requests=plans,
                        prepare_request=lambda role, *_args: (
                            apply_requests[role]
                        ),
                        invoke=lambda request, exchange: (
                            synthetic_host_result(
                                request,
                                exchange=exchange,
                                nonce_salt=request["role"],
                            )
                        ),
                        output_directory=output,
                        consumption_readback=lambda *_args: (
                            consumption_path_for(
                                apply_requests["bot_fi"]
                            ),
                            SHA_E,
                            {},
                        ),
                        checkpoint=crash,
                    )
                journal = MODULE.ControllerJournalStore(
                    output,
                    apply_requests,
                )
                if target == "completion":
                    reference = journal.document["completion"]
                else:
                    reference = journal.document["roles"]["bot_fi"][
                        "observations"
                    ][-1]
                path = Path(reference["path"])
                path.write_bytes(b"{}\n")
                os.chmod(path, 0o600)
                with self.assertRaises(
                    MODULE.FrozenFinalRestoreOrchestratorError
                ):
                    MODULE.recover_consumed_completion(
                        journal=journal,
                        consumption_readback=lambda *_args: self.fail(
                            "tamper must fail before consumption readback"
                        ),
                    )
                self.assertEqual(lease.consume_calls, [])

    def test_no_release_bytecode_is_created_by_module_import(self) -> None:
        release = Path(
            MODULE._expected_release_paths(OPERATION_ID, RELEASE_SHA)[
                "release_root"
            ]
        )
        with mock.patch.dict(
            os.environ,
            {"PYTHONDONTWRITEBYTECODE": "1"},
        ):
            command = MODULE.session_arguments(
                request_for("bot_fi"),
                ssh_identity=Path("/root/.ssh/id_ed25519"),
                known_hosts=Path("/root/.ssh/known_hosts"),
            )
        self.assertEqual(command[0], MODULE.ENV)
        self.assertIn("-B", command)
        self.assertFalse((release / "scripts/__pycache__").exists())


if __name__ == "__main__":
    unittest.main()
