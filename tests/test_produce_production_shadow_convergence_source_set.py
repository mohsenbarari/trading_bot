from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from core.sync_parity import business_snapshot_fingerprint
from scripts import produce_production_shadow_convergence_source_set as MODULE


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
OPERATION_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2945"
RELEASE_SHA = "1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"
TREE_SHA = "a" * 40
MANIFEST_SHA = "b" * 64
PLAN_SHA = "c" * 64
APPROVAL_SHA = "d" * 64
REQUEST_SHA = "e" * 64
ATTESTATION_SHA = "f" * 64
ATTESTATION_FILE_SHA = "1" * 64


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def remote_policy_material(role: str) -> tuple[bytes, dict[str, str]]:
    policy_module = MODULE.RECEIVER_POLICY
    public_key = bytes(range(32))
    document: dict[str, object] = {
        "schema": policy_module.POLICY_SCHEMA,
        "algorithm": policy_module.ALGORITHM,
        "key_id": f"{role}-convergence-01",
        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
        "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": TREE_SHA,
        "role": role,
        "not_before": "2026-07-28T11:00:00Z",
        "expires_at": "2026-07-28T13:00:00Z",
        "receiver_sha256": "8" * 64,
        "worker_sha256": "9" * 64,
        "policy_sha256": "0" * 64,
    }
    document["policy_sha256"] = hashlib.sha256(
        policy_module.policy_payload(document)
    ).hexdigest()
    policy = policy_module.parse_policy_payload(
        policy_module.canonical_json_bytes(document) + b"\n"
    )
    return (
        policy_module.canonical_json_bytes(document) + b"\n",
        {
            "policy_file_sha256": "6" * 64,
            "policy_sha256": policy.policy_sha256,
            "key_id": policy.key_id,
            "public_key_sha256": hashlib.sha256(policy.public_key).hexdigest(),
            "receiver_sha256": policy.receiver_sha256,
            "worker_sha256": policy.worker_sha256,
        },
    )


def context() -> SimpleNamespace:
    return SimpleNamespace(
        manifest={
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": TREE_SHA,
            "artifacts": {
                "cutover_approval_sha256": APPROVAL_SHA,
                "host_agent_sha256": "1" * 64,
                "host_agent_contract_sha256": "2" * 64,
                "remote_receiver_signing_policies": {
                    role: remote_policy_material(role)[1]
                    for role in MODULE.OBJECT_STORAGE_ROLES
                },
            },
            "deployment": {"controller_evidence_root": "/tmp/convergence-evidence"},
        },
        manifest_sha256=MANIFEST_SHA,
        plan_sha256=PLAN_SHA,
        journal={"started_at": (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")},
    )


def object_storage_detail(*, version_id: str = "version-1") -> dict[str, object]:
    ciphertext_sha = "2" * 64
    return {
        "provider": "arvan",
        "bucket": MODULE.PRODUCTION_BUCKET,
        "artifact_kind": "convergence-attestation",
        "object_key": (
            f"dark-standby/convergence/{OPERATION_ID}/convergence-attestation/"
            f"{'3' * 32}-{ciphertext_sha}.age"
        ),
        "version_id": version_id,
        "readback_version_id": version_id,
        "ciphertext_sha256": ciphertext_sha,
        "ciphertext_bytes": 128,
        "age_recipient_sha256": "4" * 64,
        "private": True,
        "versioned": True,
    }


def remote_receiver_attestation(
    *,
    role: str,
    detail: dict[str, object],
    observed_at: datetime = NOW,
) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": MODULE.REMOTE_RECEIVER_ATTESTATION_SCHEMA,
        "status": "received",
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": TREE_SHA,
        "manifest_sha256": MANIFEST_SHA,
        "plan_sha256": PLAN_SHA,
        "approval_sha256": APPROVAL_SHA,
        "phase": MODULE.PHASE,
        "operation": MODULE.OPERATION,
        "role": role,
        "expected_host": MODULE.CONTROLLER.EXPECTED_TOPOLOGY[role]["host"],
        "phase_started_at": context().journal["started_at"],
        "worker_request_sha256": REQUEST_SHA,
        "worker_attestation_sha256": ATTESTATION_SHA,
        "worker_attestation_file_sha256": ATTESTATION_FILE_SHA,
        "transport": "object-storage-private-versioned-age",
        "object_storage": dict(detail),
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "presigned_url_persisted": False,
        "presigned_url_logged": False,
        "contains_secret_material": False,
        "direct_fi_to_ir_transfer": False,
        "receiver_attestation_sha256": MODULE.ZERO_SHA256,
    }
    document["receiver_attestation_sha256"] = MODULE._sha256(
        {key: value for key, value in document.items() if key != "receiver_attestation_sha256"}
    )
    return document


def receipt(
    *,
    role: str,
    transport: str | None = None,
    detail: dict[str, object] | None = None,
    remote: object | None = None,
) -> dict[str, object]:
    selected_transport = transport or MODULE.TRANSPORT_BY_ROLE[role]
    if detail is None:
        if selected_transport == "object-storage-private-versioned-age":
            detail = object_storage_detail()
        elif selected_transport == "trusted-ssh-redacted-attestation":
            topology = MODULE.CONTROLLER.EXPECTED_TOPOLOGY[role]
            detail = {
                "host": topology["host"],
                "port": topology["ssh_port"],
                "user": topology["ssh_user"],
                "known_hosts_sha256": "5" * 64,
            }
        else:
            detail = {
                "source_host": MODULE.CONTROLLER.EXPECTED_TOPOLOGY[role]["host"],
                "controller_role": "bot_fi",
            }
    if remote is None and selected_transport == "object-storage-private-versioned-age":
        remote = remote_receiver_attestation(role=role, detail=detail)
    document: dict[str, object] = {
        "schema": MODULE.TRANSPORT_RECEIPT_SCHEMA,
        "status": "received",
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": TREE_SHA,
        "manifest_sha256": MANIFEST_SHA,
        "plan_sha256": PLAN_SHA,
        "approval_sha256": APPROVAL_SHA,
        "phase": MODULE.PHASE,
        "operation": MODULE.OPERATION,
        "role": role,
        "expected_host": MODULE.CONTROLLER.EXPECTED_TOPOLOGY[role]["host"],
        "phase_started_at": context().journal["started_at"],
        "request_sha256": REQUEST_SHA,
        "attestation_sha256": ATTESTATION_SHA,
        "attestation_file_sha256": ATTESTATION_FILE_SHA,
        "transport": selected_transport,
        "payload_class": "redacted-attestation-json",
        "transport_detail": detail,
        "remote_receiver_attestation": remote,
        "remote_receiver_policy_file_sha256": (
            "6" * 64 if selected_transport == "object-storage-private-versioned-age" else None
        ),
        "remote_receiver_signed_attestation_file_sha256": (
            "7" * 64 if selected_transport == "object-storage-private-versioned-age" else None
        ),
        "received_at": NOW.isoformat().replace("+00:00", "Z"),
        "direct_fi_to_ir_transfer": False,
        "transport_receipt_sha256": MODULE.ZERO_SHA256,
    }
    document["transport_receipt_sha256"] = MODULE._receipt_digest(document)
    return document


def signed_receipt(*, role: str) -> dict[str, object]:
    document = receipt(role=role)
    document["remote_receiver_attestation"] = None
    document["transport_receipt_sha256"] = MODULE._receipt_digest(document)
    return document


def attestation_record() -> SimpleNamespace:
    return SimpleNamespace(
        document={"attestation_sha256": ATTESTATION_SHA},
        sha256=ATTESTATION_FILE_SHA,
    )


def compose_execution_proof() -> dict[str, object]:
    return {
        "execution_plan_sha256": "a" * 64,
        "receipt_sha256": "b" * 64,
        "container_id_sha256": "c" * 64,
        "network_id_sha256": "d" * 64,
        "cleanup_verified": True,
    }


def runtime_parity(*, business_hash: str = "7" * 64) -> dict[str, object]:
    record = {
        "identity_hash": "6" * 64,
        "business_hash": business_hash,
        "local_only_hash": "8" * 64,
        "volatile_hash": "9" * 64,
    }
    table = {
        "table": "offers",
        "row_count": 1,
        "truncated": False,
        "duplicate_identity_count": 0,
        "duplicate_identity_hashes": [],
        "records_hash": canonical_hash([record]),
        "business_records_hash": canonical_hash(
            [{"identity_hash": record["identity_hash"], "business_hash": record["business_hash"]}]
        ),
        "records": [record],
    }
    return {
        "status": "ok",
        "schema_version": 1,
        "mode": "deep",
        "table_count": 1,
        "max_rows_per_table": 10,
        "tables": {"offers": table},
    }


def runtime_snapshot(role: str, *, business_hash: str = "7" * 64) -> dict[str, object]:
    parity = runtime_parity(business_hash=business_hash)
    peers = [candidate for candidate in MODULE.RUNTIME_ROLES if candidate != role]
    dr: dict[str, object] = {
        "producer_epoch": 1,
        "source_streams": [
            {
                "destination_site": peer,
                "source_sequence": 0,
                "source_transaction_hash": MODULE.ZERO_SHA256,
            }
            for peer in peers
        ],
        "destination_streams": [
            {
                "origin_site": peer,
                "producer_epoch": 1,
                "received_sequence": 0,
                "applied_sequence": 0,
                "received_transaction_hash": MODULE.ZERO_SHA256,
                "applied_transaction_hash": MODULE.ZERO_SHA256,
            }
            for peer in peers
        ],
        "unresolved_conflict_count": 0,
    }
    dr["dr_state_sha256"] = MODULE._sha256(dr)
    database: dict[str, object] = {
        "table_set_sha256": MODULE._sha256(["offers"]),
        "business_fingerprint_sha256": business_snapshot_fingerprint(parity),
        "row_count": 1,
        "table_count": 1,
        "redacted_snapshot_sha256": MODULE._sha256(parity),
    }
    database["database_state_sha256"] = MODULE._sha256(database)
    return {
        "captured_at": NOW.isoformat().replace("+00:00", "Z"),
        "database": database,
        "redacted_parity_snapshot": parity,
        "dr": dr,
    }


def runtime_ingress(role: str, *, business_hash: str = "7" * 64) -> MODULE.Ingress:
    return MODULE.Ingress(
        role=role,
        request=SimpleNamespace(document={"request_sha256": REQUEST_SHA}, sha256=REQUEST_SHA),
        attestation=SimpleNamespace(
            document={
                "attestation_sha256": ATTESTATION_SHA,
                "runtime_snapshot": runtime_snapshot(role, business_hash=business_hash),
            },
            sha256=ATTESTATION_FILE_SHA,
        ),
        receipt=SimpleNamespace(
            document={"transport": MODULE.TRANSPORT_BY_ROLE[role]},
            sha256="0" * 64,
        ),
        observed_at=NOW,
        captured_at=NOW,
    )


def worker_request(role: str) -> dict[str, object]:
    return MODULE.WORKER.build_request(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        release_tree_sha=TREE_SHA,
        manifest_sha256=MANIFEST_SHA,
        runtime_target_binding_sha256=None if role == "witness" else "a" * 64,
        plan_sha256=PLAN_SHA,
        approval_sha256=APPROVAL_SHA,
        role=role,
        expected_host=MODULE.CONTROLLER.EXPECTED_TOPOLOGY[role]["host"],
        phase_started_at=NOW - timedelta(minutes=10),
        worker_sha256="9" * 64,
        max_rows_per_table=10,
    )


def host_identity_proof(
    request: dict[str, object],
    *,
    observed_host: str | None = None,
) -> dict[str, object]:
    expected_host = str(request["expected_host"])
    document: dict[str, object] = {
        "schema": MODULE.WORKER.HOST_IDENTITY_PROOF_SCHEMA,
        "expected_host": expected_host,
        "observed_host": observed_host or expected_host,
        "address_family": "inet",
        "interface": "eth0",
        "collector": "kernel-ip-json",
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "host_identity_proof_sha256": MODULE.WORKER.ZERO_SHA256,
    }
    document["host_identity_proof_sha256"] = MODULE.WORKER._host_identity_proof_digest(document)
    return document


def ingress_with_host_identity_proof(role: str) -> MODULE.Ingress:
    request = worker_request(role)
    return MODULE.Ingress(
        role=role,
        request=SimpleNamespace(
            document=request,
            sha256=REQUEST_SHA,
            path=Path(f"/tmp/convergence/{role}.request.json"),
        ),
        attestation=SimpleNamespace(
            document={
                "attestation_sha256": ATTESTATION_SHA,
                "host_identity_proof": host_identity_proof(request),
                "compose_execution": (
                    compose_execution_proof() if role in MODULE.RUNTIME_ROLES else None
                ),
            },
            sha256=ATTESTATION_FILE_SHA,
            path=Path(f"/tmp/convergence/{role}.attestation.json"),
        ),
        receipt=SimpleNamespace(
            document={"transport": MODULE.TRANSPORT_BY_ROLE[role]},
            sha256="0" * 64,
            path=Path(f"/tmp/convergence/{role}.receipt.json"),
        ),
        observed_at=NOW,
        captured_at=None,
    )


class ProductionShadowConvergenceSourceSetProducerTests(unittest.TestCase):
    def validate_receipt(self, document: dict[str, object], *, role: str) -> None:
        MODULE._validate_receipt(
            document,
            context=context(),
            role=role,
            request={"request_sha256": REQUEST_SHA},
            attestation=attestation_record(),
            now=NOW,
        )

    def validate_signed_receipt(
        self,
        document: dict[str, object],
        *,
        role: str,
        provenance: dict[str, object] | None = None,
        error: Exception | None = None,
        policy_payload: bytes | None = None,
    ) -> mock.Mock:
        policy = SimpleNamespace(payload=policy_payload or remote_policy_material(role)[0])
        signed = SimpleNamespace(payload=b'{"signed":"root-only"}\n')

        def read_record(reference: object, *, label: str) -> SimpleNamespace:
            digest = getattr(reference, "sha256")
            if digest == document["remote_receiver_policy_file_sha256"]:
                return policy
            if digest == document["remote_receiver_signed_attestation_file_sha256"]:
                return signed
            self.fail(f"unexpected secure record read: {label}")

        verified = mock.Mock(
            return_value=provenance
            or remote_receiver_attestation(
                role=role,
                detail=dict(document["transport_detail"]),
            )
        )
        if error is not None:
            verified.side_effect = error
        with (
            mock.patch.object(MODULE, "_read_record", side_effect=read_record),
            mock.patch.object(
                MODULE.REMOTE_PROVENANCE,
                "verify_remote_receiver_provenance",
                verified,
            ),
        ):
            MODULE._validate_receipt(
                document,
                context=context(),
                role=role,
                request={"request_sha256": REQUEST_SHA},
                attestation=attestation_record(),
                now=NOW,
            )
        return verified

    def test_plan_is_no_contact_and_requires_object_storage_for_witness(self) -> None:
        plan = MODULE.build_plan()

        self.assertEqual(plan["default_action"], "plan")
        self.assertFalse(plan["producer_network_io"])
        self.assertFalse(plan["producer_docker_io"])
        self.assertFalse(plan["producer_ssh_io"])
        self.assertFalse(plan["remote_receiver_authentication_available"])
        self.assertFalse(plan["controller_producer_exact_release_available"])
        self.assertEqual(
            plan["controller_producer_exact_release_requirement"],
            MODULE.CONTROLLER_PRODUCER_EXACT_RELEASE_REQUIREMENT,
        )
        self.assertEqual(
            plan["required_transport"]["witness"],
            "object-storage-private-versioned-age",
        )
        self.assertEqual(
            plan["object_storage_private_versioned_age_roles"],
            ["webapp_ir", "witness"],
        )

    def test_canonical_incoming_path_does_not_accept_caller_selected_paths(self) -> None:
        path = MODULE.canonical_incoming_path(
            context(),
            kind="attestations",
            role="webapp_ir",
            digest="a" * 64,
        )

        self.assertTrue(str(path).endswith("incoming/attestations/webapp_ir." + "a" * 64 + ".json"))
        with self.assertRaises(MODULE.ConvergenceSourceSetProducerError):
            MODULE.canonical_incoming_path(
                context(),
                kind="../../outside",
                role="webapp_ir",
                digest="a" * 64,
            )

    def test_legacy_unsigned_controller_object_storage_receipt_is_rejected(self) -> None:
        document = receipt(role="webapp_ir")

        with self.assertRaisesRegex(
            MODULE.ConvergenceSourceSetProducerError,
            "legacy unsigned",
        ):
            self.validate_receipt(document, role="webapp_ir")

    def test_signed_receiver_inputs_are_read_by_digest_and_verified_by_adapter(self) -> None:
        document = signed_receipt(role="webapp_ir")

        verified = self.validate_signed_receipt(document, role="webapp_ir")

        kwargs = verified.call_args.kwargs
        self.assertEqual(kwargs["policy_payload"], remote_policy_material("webapp_ir")[0])
        self.assertEqual(kwargs["attestation_payload"], b'{"signed":"root-only"}\n')
        self.assertEqual(kwargs["expected"].request_sha256, REQUEST_SHA)
        self.assertEqual(kwargs["expected"].worker_attestation_file_sha256, ATTESTATION_FILE_SHA)
        self.assertIs(kwargs["verify_ed25519"], MODULE.ED25519.verify_ed25519)

    def test_signed_receiver_policy_request_or_version_drift_fails_closed(self) -> None:
        document = signed_receipt(role="webapp_ir")
        for drift in ("policy", "request", "version"):
            with self.subTest(drift=drift):
                with self.assertRaisesRegex(
                    MODULE.ConvergenceSourceSetProducerError,
                    "signed provenance is invalid",
                ):
                    self.validate_signed_receipt(
                        document,
                        role="webapp_ir",
                        error=MODULE.REMOTE_PROVENANCE.RemoteReceiverProvenanceError(drift),
                    )

    def test_receipt_cannot_select_or_substitute_a_remote_receiver_policy(self) -> None:
        document = signed_receipt(role="webapp_ir")
        document["remote_receiver_policy_file_sha256"] = "a" * 64
        document["transport_receipt_sha256"] = MODULE._receipt_digest(document)
        with self.assertRaisesRegex(
            MODULE.ConvergenceSourceSetProducerError,
            "not immutable-manifest pinned",
        ):
            self.validate_signed_receipt(document, role="webapp_ir")

        document = signed_receipt(role="webapp_ir")
        payload = remote_policy_material("webapp_ir")[0]
        forged = json.loads(payload)
        forged["key_id"] = "forged-controller-key"
        forged["policy_sha256"] = hashlib.sha256(
            MODULE.RECEIVER_POLICY.policy_payload(forged)
        ).hexdigest()
        forged_payload = MODULE.RECEIVER_POLICY.canonical_json_bytes(forged) + b"\n"
        with self.assertRaisesRegex(
            MODULE.ConvergenceSourceSetProducerError,
            "policy anchor differs",
        ):
            self.validate_signed_receipt(
                document,
                role="webapp_ir",
                policy_payload=forged_payload,
            )

    def test_forged_controller_signed_receiver_receipt_is_rejected(self) -> None:
        document = signed_receipt(role="webapp_ir")
        policy_payload, _contract = remote_policy_material("webapp_ir")
        policy = MODULE.RECEIVER_POLICY.parse_policy_payload(policy_payload)
        signed = MODULE.REMOTE_PROVENANCE.ATTESTATION.build_attestation(
            policy=policy,
            manifest_sha256=MANIFEST_SHA,
            plan_sha256=PLAN_SHA,
            approval_sha256=APPROVAL_SHA,
            phase=MODULE.PHASE,
            operation=MODULE.OPERATION,
            expected_host=MODULE.CONTROLLER.EXPECTED_TOPOLOGY["webapp_ir"]["host"],
            phase_started_at=context().journal["started_at"],
            request_sha256=REQUEST_SHA,
            worker_attestation_sha256=ATTESTATION_SHA,
            worker_attestation_file_sha256=ATTESTATION_FILE_SHA,
            object_storage=object_storage_detail(),
            observed_at=NOW.isoformat().replace("+00:00", "Z"),
            sign_ed25519=lambda _payload: b"s" * 64,
        )

        def read_record(reference: object, *, label: str) -> SimpleNamespace:
            if getattr(reference, "sha256") == "6" * 64:
                return SimpleNamespace(payload=policy_payload)
            if getattr(reference, "sha256") == "7" * 64:
                return SimpleNamespace(payload=signed.payload)
            self.fail(f"unexpected secure record read: {label}")

        with mock.patch.object(MODULE, "_read_record", side_effect=read_record):
            with self.assertRaisesRegex(
                MODULE.ConvergenceSourceSetProducerError,
                "signed provenance is invalid",
            ):
                MODULE._validate_receipt(
                    document,
                    context=context(),
                    role="webapp_ir",
                    request={"request_sha256": REQUEST_SHA},
                    attestation=attestation_record(),
                    now=NOW,
                )

    def test_controller_exact_release_requirement_blocks_before_any_publication(self) -> None:
        digests = {role: "a" * 64 for role in MODULE.ROLES}
        with (
            mock.patch.object(MODULE, "_validate_context"),
            mock.patch.object(MODULE, "_ensure_layout"),
            mock.patch.object(MODULE, "_utcnow", return_value=NOW),
            mock.patch.object(MODULE, "_validate_ingress") as validate_ingress,
            mock.patch.object(MODULE, "_publish_observation") as publish_observation,
            mock.patch.object(MODULE, "_publish_role_validation") as publish_validation,
            mock.patch.object(MODULE, "_publish_availability") as publish_availability,
        ):
            with self.assertRaisesRegex(
                MODULE.ControllerProducerExactReleaseUnavailable,
                MODULE.CONTROLLER_PRODUCER_EXACT_RELEASE_REQUIREMENT,
            ):
                MODULE.produce(
                    context(),
                    request_digests=digests,
                    attestation_digests=digests,
                    receipt_digests=digests,
                )

        validate_ingress.assert_not_called()
        publish_observation.assert_not_called()
        publish_validation.assert_not_called()
        publish_availability.assert_not_called()

    def test_pure_observation_loader_requires_all_five_collectors_before_reading(self) -> None:
        with mock.patch.object(MODULE, "_read_record") as read_record:
            with self.assertRaisesRegex(
                MODULE.ConvergenceSourceSetProducerError,
                "cover exactly five",
            ):
                MODULE._load_pure_observations(
                    context(),
                    digests={"blob_roundtrip": "a" * 64},
                    now=NOW,
                )
        read_record.assert_not_called()

    def test_pure_observation_loader_derives_canonical_readback_paths(self) -> None:
        digests = {
            label: character * 64
            for label, character in zip(MODULE.PURE_OBSERVATIONS, "abcde", strict=True)
        }
        documents = {label: {"label": label} for label in MODULE.PURE_OBSERVATIONS}
        references: list[object] = []

        def read_record(reference: object, *, label: str) -> SimpleNamespace:
            references.append(reference)
            observation = label.removesuffix(" pure observation")
            self.assertEqual(
                getattr(reference, "path"),
                MODULE.canonical_pure_observation_path(
                    context(), label=observation, digest=digests[observation]
                ),
            )
            self.assertEqual(getattr(reference, "sha256"), digests[observation])
            return SimpleNamespace(document=documents[observation])

        with (
            mock.patch.object(MODULE, "_read_record", side_effect=read_record),
            mock.patch.object(
                MODULE,
                "_validate_pure_observation",
                side_effect=lambda document, **_kwargs: document,
            ) as validate,
        ):
            loaded = MODULE._load_pure_observations(
                context(), digests=digests, now=NOW
            )

        self.assertEqual(
            {label: record.document for label, record in loaded.items()}, documents
        )
        self.assertEqual(len(references), len(MODULE.PURE_OBSERVATIONS))
        self.assertEqual(validate.call_count, len(MODULE.PURE_OBSERVATIONS))

    def test_pure_observation_cli_mapping_fails_closed_when_incomplete(self) -> None:
        with self.assertRaisesRegex(
            MODULE.ConvergenceSourceSetUnavailable,
            "collectors or proofs are incomplete",
        ):
            MODULE._parse_pure_observation_mapping(
                ["blob_roundtrip=" + "a" * 64]
            )

    def test_controller_exact_release_accepts_only_held_git_bound_producer_and_launcher(self) -> None:
        contract = SimpleNamespace(
            release_root_descriptor=4,
            producer_descriptor=5,
            launcher_descriptor=3,
        )
        with (
            mock.patch.object(
                MODULE,
                "_require_controller_producer_launcher_contract",
                return_value=contract,
            ),
            mock.patch.object(
                MODULE,
                "_held_controller_release_git_text",
                side_effect=[RELEASE_SHA, TREE_SHA],
            ),
            mock.patch.object(MODULE, "_assert_controller_producer_regular_descriptor"),
            mock.patch.object(
                MODULE.WORKER,
                "_verified_release_file_sha256",
                side_effect=["1" * 64, "2" * 64],
            ),
            mock.patch.object(
                MODULE,
                "_held_controller_release_blob_sha256",
                side_effect=["1" * 64, "2" * 64],
            ),
        ):
            MODULE._require_controller_producer_exact_release(context())

    def test_controller_exact_release_rejects_held_producer_blob_drift(self) -> None:
        contract = SimpleNamespace(
            release_root_descriptor=4,
            producer_descriptor=5,
            launcher_descriptor=3,
        )
        with (
            mock.patch.object(
                MODULE,
                "_require_controller_producer_launcher_contract",
                return_value=contract,
            ),
            mock.patch.object(
                MODULE,
                "_held_controller_release_git_text",
                side_effect=[RELEASE_SHA, TREE_SHA],
            ),
            mock.patch.object(MODULE, "_assert_controller_producer_regular_descriptor"),
            mock.patch.object(
                MODULE.WORKER,
                "_verified_release_file_sha256",
                side_effect=["1" * 64, "2" * 64],
            ),
            mock.patch.object(
                MODULE,
                "_held_controller_release_blob_sha256",
                side_effect=["3" * 64, "2" * 64],
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.ControllerProducerExactReleaseUnavailable,
                "differs from the exact release",
            ):
                MODULE._require_controller_producer_exact_release(context())

    def test_produce_has_no_public_clock_override(self) -> None:
        digests = {role: "a" * 64 for role in MODULE.ROLES}

        with self.assertRaises(TypeError):
            MODULE.produce(
                context(),
                request_digests=digests,
                attestation_digests=digests,
                receipt_digests=digests,
                now=NOW,
            )

    def test_signed_receiver_version_must_match_transport_receipt(self) -> None:
        document = receipt(role="webapp_ir")
        remote = dict(document["remote_receiver_attestation"])
        remote_detail = dict(remote["object_storage"])
        remote_detail["version_id"] = "another-version"
        remote_detail["readback_version_id"] = "another-version"
        remote["object_storage"] = remote_detail
        remote["receiver_attestation_sha256"] = MODULE._sha256(
            {key: value for key, value in remote.items() if key != "receiver_attestation_sha256"}
        )
        document["remote_receiver_attestation"] = remote
        document["transport_receipt_sha256"] = MODULE._receipt_digest(document)

        with self.assertRaisesRegex(
            MODULE.ConvergenceSourceSetProducerError,
            "legacy unsigned",
        ):
            self.validate_receipt(document, role="webapp_ir")

    def test_witness_ssh_receipt_is_rejected(self) -> None:
        document = receipt(
            role="witness",
            transport="trusted-ssh-redacted-attestation",
            remote=None,
        )

        with self.assertRaisesRegex(
            MODULE.ConvergenceSourceSetProducerError,
            "transport receipt fields differ",
        ):
            self.validate_receipt(document, role="witness")

    def test_signed_receiver_version_mismatch_from_adapter_is_rejected(self) -> None:
        document = signed_receipt(role="webapp_ir")
        provenance = remote_receiver_attestation(
            role="webapp_ir",
            detail=object_storage_detail(version_id="different-version"),
        )
        with self.assertRaisesRegex(
            MODULE.ConvergenceSourceSetProducerError,
            "VersionId/read-back differs",
        ):
            self.validate_signed_receipt(
                document,
                role="webapp_ir",
                provenance=provenance,
            )

    def test_database_and_dr_are_recomputed_from_redacted_runtime_records(self) -> None:
        ingresses = {role: runtime_ingress(role) for role in MODULE.RUNTIME_ROLES}

        database = MODULE._database_observation(context(), ingresses, observed_at=NOW)
        dr = MODULE._dr_observation(context(), ingresses, observed_at=NOW)

        self.assertEqual(database["mismatch_count"], 0)
        self.assertEqual(len(database["comparisons"]), 4)
        self.assertEqual(dr["conflict_count"], 0)
        self.assertEqual(len(dr["streams"]), 6)

        drifted = dict(ingresses)
        drifted["webapp_ir"] = runtime_ingress("webapp_ir", business_hash="a" * 64)
        with self.assertRaisesRegex(MODULE.ConvergenceSourceSetProducerError, "not converged"):
            MODULE._database_observation(context(), drifted, observed_at=NOW)

    def test_capture_skew_cannot_be_overridden_by_caller_claims(self) -> None:
        ingresses = {role: runtime_ingress(role) for role in MODULE.RUNTIME_ROLES}
        witness = MODULE.Ingress(
            role="witness",
            request=SimpleNamespace(document={"request_sha256": REQUEST_SHA}, sha256=REQUEST_SHA),
            attestation=SimpleNamespace(document={"attestation_sha256": ATTESTATION_SHA}, sha256=ATTESTATION_FILE_SHA),
            receipt=SimpleNamespace(document={}, sha256="0" * 64),
            observed_at=NOW,
            captured_at=None,
        )
        ingresses["witness"] = witness
        self.assertEqual(
            MODULE._validate_capture_times(ingresses, context=context(), now=NOW),
            NOW,
        )

        ingresses["webapp_ir"] = replace(
            ingresses["webapp_ir"],
            observed_at=NOW - MODULE.BRIDGE.MAX_SOURCE_SKEW - timedelta(seconds=1),
            captured_at=NOW - MODULE.BRIDGE.MAX_SOURCE_SKEW - timedelta(seconds=1),
        )
        with self.assertRaisesRegex(MODULE.ConvergenceSourceSetProducerError, "skew"):
            MODULE._validate_capture_times(ingresses, context=context(), now=NOW)

    def test_role_validation_uses_attested_local_host_proof_not_topology_value(self) -> None:
        ingress = ingress_with_host_identity_proof("bot_fi")
        mutated_topology = dict(MODULE.CONTROLLER.EXPECTED_TOPOLOGY["bot_fi"])
        mutated_topology["host"] = "203.0.113.77"
        with mock.patch.dict(
            MODULE.CONTROLLER.EXPECTED_TOPOLOGY,
            {"bot_fi": mutated_topology},
        ):
            document = MODULE._role_validation_document(context(), ingress=ingress)
        proof = ingress.attestation.document["host_identity_proof"]
        self.assertEqual(document["expected_host"], proof["expected_host"])
        self.assertEqual(document["observed_host"], proof["observed_host"])
        self.assertEqual(document["observed_at"], proof["observed_at"])
        self.assertTrue(document["host_identity_observed"])
        self.assertEqual(document["schema"], MODULE.CONVERGENCE_ROLE_VALIDATION_SCHEMA)
        self.assertEqual(document["worker_request"]["sha256"], ingress.request.sha256)
        self.assertEqual(document["worker_attestation"]["sha256"], ingress.attestation.sha256)
        self.assertEqual(document["transport_receipt"]["sha256"], ingress.receipt.sha256)
        self.assertEqual(
            document["host_identity_proof_sha256"],
            proof["host_identity_proof_sha256"],
        )
        self.assertEqual(
            document["request_sha256"],
            document["provenance_closure_sha256"],
        )
        self.assertEqual(
            document["request_sha256"],
            MODULE._role_provenance_closure_digest(document),
        )
        self.assertEqual(
            document["compose_execution"],
            ingress.attestation.document["compose_execution"],
        )

    def test_runtime_role_validation_requires_compose_execution_proof(self) -> None:
        ingress = ingress_with_host_identity_proof("bot_fi")
        hostile_ingress = replace(
            ingress,
            attestation=SimpleNamespace(
                document={
                    key: value
                    for key, value in ingress.attestation.document.items()
                    if key != "compose_execution"
                },
                sha256=ingress.attestation.sha256,
                path=ingress.attestation.path,
            ),
        )
        with self.assertRaisesRegex(
            MODULE.ConvergenceSourceSetProducerError,
            "Compose execution proof is invalid",
        ):
            MODULE._role_validation_document(context(), ingress=hostile_ingress)

    def test_role_validation_rejects_mismatched_host_identity_proof(self) -> None:
        ingress = ingress_with_host_identity_proof("bot_fi")
        hostile_proof = dict(ingress.attestation.document["host_identity_proof"])
        hostile_proof["observed_host"] = "203.0.113.77"
        hostile_proof["host_identity_proof_sha256"] = MODULE.WORKER._host_identity_proof_digest(
            hostile_proof
        )
        hostile_attestation = SimpleNamespace(
            document={
                **ingress.attestation.document,
                "host_identity_proof": hostile_proof,
            },
            sha256=ingress.attestation.sha256,
        )
        hostile_ingress = replace(ingress, attestation=hostile_attestation)
        with self.assertRaisesRegex(
            MODULE.ConvergenceSourceSetProducerError,
            "local host identity proof is invalid",
        ):
            MODULE._role_validation_document(context(), ingress=hostile_ingress)


if __name__ == "__main__":
    unittest.main()
