from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import orchestrate_production_shadow_prepared_clone_inventory as MODULE
from scripts import production_shadow_global_docker_inventory_agent as INVENTORY


CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
OPERATION_ID = "8fb08095-7a9e-4a92-9fa9-3f9a301b2945"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40
BASE = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _inputs() -> MODULE.CollectionInputs:
    return MODULE.CollectionInputs(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        release_tree_sha=RELEASE_TREE_SHA,
        agent_sha256="1" * 64,
        roles={
            role: MODULE.RoleBinding(
                contract_worker_sha256=str(index + 2) * 64,
                role_manifest_sha256=str(index + 5) * 64,
            )
            for index, role in enumerate(MODULE.ROLES)
        },
    )


def _controller_inputs() -> MODULE.CollectionInputs:
    return MODULE.CollectionInputs(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        release_tree_sha=RELEASE_TREE_SHA,
        agent_sha256="1" * 64,
        roles={
            "bot_fi": MODULE.RoleBinding(
                contract_worker_sha256="2" * 64,
                role_manifest_sha256="5" * 64,
            ),
            "webapp_fi": MODULE.RoleBinding(
                contract_worker_sha256="2" * 64,
                role_manifest_sha256="6" * 64,
            ),
            "webapp_ir": MODULE.RoleBinding(
                contract_worker_sha256="4" * 64,
                role_manifest_sha256="7" * 64,
            ),
        },
    )


def _controller_context(root: Path) -> MODULE.ControllerContext:
    inputs = _controller_inputs()
    return MODULE.ControllerContext(
        manifest_path=root / "manifest.json",
        approval_path=root / "approval.json",
        approval_policy_path=root / "policy.json",
        release_closure_path=root / "release-closure.json",
        prepare_metadata_path=root / "prepare.json",
        ssh_identity=root / "identity",
        known_hosts=root / "known-hosts",
        manifest={
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
        },
        manifest_sha256="8" * 64,
        cutover_plan_sha256="9" * 64,
        approval_sha256="a" * 64,
        approval_policy_sha256="b" * 64,
        release_closure_sha256="f" * 64,
        prepare_metadata_sha256="c" * 64,
        release_artifact_sha256={
            "inventory_agent": "1" * 64,
            "finland_precommit_worker": "2" * 64,
            "wa_ir_operation_worker": "4" * 64,
        },
        release_artifact_git_blob={
            "inventory_agent": "1" * 40,
            "finland_precommit_worker": "2" * 40,
            "wa_ir_operation_worker": "4" * 40,
        },
        ssh_identity_sha256="d" * 64,
        known_hosts_sha256="e" * 64,
        output_root=root,
        collection_inputs=inputs,
    )


def _controller_source_documents(
    *,
    approval_payload: bytes,
    policy_payload: bytes,
    evidence_root: Path,
) -> tuple[dict, dict]:
    role_materials = {
        role: {
            "sha256": str(index + 1) * 64,
            "bytes": 100 + index,
            "transport": MODULE.PREPARE.ROLE_TRANSPORTS[role],
            "format": MODULE.PREPARE.ROLE_FORMATS[role],
        }
        for index, role in enumerate(MODULE.PREPARE.ALL_ROLES)
    }
    runtime_ids = {
        role: {
            kind: f"sha256:{str(index + kind_index + 1)[-1] * 64}"
            for kind_index, kind in enumerate(MODULE.PREPARE.IMAGE_KINDS)
        }
        for index, role in enumerate(MODULE.ROLES)
    }
    image_artifacts = {
        kind: {"marker": kind}
        for kind in MODULE.PREPARE.IMAGE_KINDS
    }
    runtime_target_descriptor = {
        "schema": MODULE.PREPARE.CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
        "filename": MODULE.PREPARE.CONVERGENCE_RUNTIME_TARGETS_FILENAME,
        "sha256": "c" * 64,
        "bytes": 1024,
        "target_set_sha256": "d" * 64,
        "roles": list(MODULE.PREPARE.DOCKER_ROLES),
    }
    manifest = {
        "schema": MODULE.CONTROLLER.MANIFEST_SCHEMA,
        "capabilities": list(
            MODULE.runtime_targets.RUNTIME_TARGET_CAPABILITIES
        ),
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "topology": {
            role: dict(MODULE.CONTROLLER.EXPECTED_TOPOLOGY[role])
            for role in MODULE.CONTROLLER.EXPECTED_TOPOLOGY
        },
        "deployment": {
            "shadow_root": str(
                MODULE.INVENTORY.PROJECT_ROOT_PREFIX / OPERATION_ID
            ),
            "controller_evidence_root": str(evidence_root),
        },
        "artifacts": {
            "cutover_approval_sha256": hashlib.sha256(
                approval_payload
            ).hexdigest(),
            "human_approval_policy_sha256": hashlib.sha256(
                policy_payload
            ).hexdigest(),
            "release_bundle_sha256": "9" * 64,
            "release_bundle_bytes": 123,
            "image_artifacts": image_artifacts,
            "shadow_compose_sha256": "f" * 64,
            "role_materials": role_materials,
            "role_runtime_image_ids": runtime_ids,
            "convergence_runtime_targets": runtime_target_descriptor,
        },
    }
    metadata = {
        "schema": MODULE.PREPARE.SET_SCHEMA,
        "capabilities": list(
            MODULE.runtime_targets.RUNTIME_TARGET_CAPABILITIES
        ),
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "canonical_compose_sha256": "f" * 64,
        "dr_ca_sha256": "a" * 64,
        "dr_tls_attestation_sha256": "b" * 64,
        "dr_tls_attested_at_epoch": 1_785_283_200,
        "roles": {
            role: {
                "filename": MODULE.PREPARE.ROLE_ARCHIVE_NAMES[role],
                **role_materials[role],
                "internal_manifest_sha256": str(index + 5) * 64,
                "stage_operation_manifest_sha256": str(index + 1) * 64,
                "stage_attestation_sha256": str(index + 2) * 64,
            }
            for index, role in enumerate(MODULE.PREPARE.ALL_ROLES)
        },
        "controller_bindings": {
            "role_materials": role_materials,
            "role_runtime_image_ids": runtime_ids,
            "convergence_runtime_targets": runtime_target_descriptor,
        },
        "activation_secrets_included": False,
        "precommit_manifest_bound": False,
    }
    return manifest, metadata


def _requests(
    *,
    challenge: str = "9" * 64,
    issued_at: datetime = BASE,
) -> dict[str, dict]:
    inputs = _inputs()
    return MODULE._request_set(
        inputs,
        challenge=challenge,
        issued_at=issued_at,
        expires_at=issued_at
        + timedelta(seconds=MODULE.REQUEST_LIFETIME_SECONDS),
    )


def _response(
    request: dict,
    *,
    captured_at: datetime,
    marker: str,
) -> dict:
    digest = marker * 64
    running = request["expected_database_state"] == "running-healthy"

    def baseline(field: str) -> str:
        return digest if running else request[field]

    result = {
        "schema": INVENTORY.PREPARED_RESPONSE_SCHEMA,
        "status": "captured-prepared-stable",
        **{
            field: request[field]
            for field in (
                "action",
                "campaign_id",
                "operation_id",
                "release_sha",
                "release_tree_sha",
                "role",
                "expected_host",
                "controller_challenge_sha256",
                "issued_at",
                "expires_at",
                "expected_database_state",
                "baseline_response_sha256",
                "contract_kind",
                "project_base",
                "project_name",
                "request_binding_sha256",
                "role_manifest_sha256",
            )
        },
        "observed_host_ipv4": [request["expected_host"]],
        "captured_at": INVENTORY.canonical_utc_timestamp(captured_at),
        "prepared_container_id": (
            marker * 64
            if running
            else request["expected_prepared_container_id"]
        ),
        "prepared_network_id": (
            chr(ord(marker) + 3) * 64
            if running
            else request["expected_prepared_network_id"]
        ),
        "prepared_container_identity_sha256": digest,
        "prepared_container_state_sha256": digest,
        "prepared_container_metadata_sha256": digest,
        "prepared_network_identity_sha256": baseline(
            "expected_prepared_network_identity_sha256"
        ),
        "prepared_network_state_sha256": digest,
        "prepared_network_metadata_sha256": baseline(
            "expected_prepared_network_metadata_sha256"
        ),
        "prepared_config_sha256": baseline(
            "expected_prepared_config_sha256"
        ),
        "prepared_environment_sha256": digest,
        "prepared_environment_entry_count": 2,
        "prepared_compose_config_sha256": baseline(
            "expected_prepared_compose_config_sha256"
        ),
        "prepared_host_config_sha256": baseline(
            "expected_prepared_host_config_sha256"
        ),
        "prepared_mounts_sha256": baseline(
            "expected_prepared_mounts_sha256"
        ),
        "prepared_network_attachment_sha256": digest,
        "prepared_redis_identity_sha256": baseline(
            "expected_prepared_redis_identity_sha256"
        ),
        "prepared_redis_chain_metadata_sha256": baseline(
            "expected_prepared_redis_chain_metadata_sha256"
        ),
        "prepared_redis_metadata_sha256": baseline(
            "expected_prepared_redis_metadata_sha256"
        ),
        "prepared_redis_target_count": 1,
        "prepared_redis_unsafe_path_count": 0,
        "prepared_redis_entry_count": 0,
        "prepared_redis_pristine": True,
        "inventory_root_sha256": digest,
        "inventory_identity_root_sha256": digest,
        "inventory_state_root_sha256": digest,
        "inventory_metadata_root_sha256": digest,
        "resource_counts": {
            "container": 2,
            "network": 2,
            "volume": 1,
            "image": 1,
        },
        "non_operation_inventory_root_sha256": baseline(
            "expected_non_operation_inventory_root_sha256"
        ),
        "non_operation_identity_root_sha256": baseline(
            "expected_non_operation_identity_root_sha256"
        ),
        "non_operation_state_root_sha256": baseline(
            "expected_non_operation_state_root_sha256"
        ),
        "non_operation_metadata_root_sha256": baseline(
            "expected_non_operation_metadata_root_sha256"
        ),
        "non_operation_resource_counts": (
            {
                "container": 1,
                "network": 1,
                "volume": 1,
                "image": 1,
            }
            if running
            else request["expected_non_operation_resource_counts"]
        ),
        "operation_resource_root_sha256": digest,
        "operation_resource_counts": {
            "container": 1,
            "network": 1,
            "volume": 0,
            "image": 0,
        },
        "stable_capture_count": 2,
        "prepared_database_running": running,
        "prepared_database_healthy": running,
        "descriptors_returned": False,
        "environment_values_returned": False,
        "path_descriptors_returned": False,
        "docker_read_only": True,
        "network_io_performed": False,
        "filesystem_mutated": False,
    }
    result["response_sha256"] = hashlib.sha256(
        INVENTORY.canonical_json(result)
    ).hexdigest()
    return result


def _response_set(
    requests: dict[str, dict],
) -> tuple[dict[str, dict], dict[str, tuple[datetime, datetime]]]:
    responses = {}
    command_times = {}
    for index, role in enumerate(MODULE.ROLES):
        started = BASE + timedelta(seconds=index * 2 + 1)
        captured = started + timedelta(microseconds=500_000)
        completed = started + timedelta(seconds=1)
        responses[role] = _response(
            requests[role],
            captured_at=captured,
            marker=chr(ord("a") + index),
        )
        command_times[role] = (started, completed)
    return responses, command_times


class ExternalLiveness:
    def __enter__(self) -> int:
        self.read_fd, write_fd = os.pipe()
        stop_read, self.stop_write = os.pipe()
        self.pid = os.fork()
        if self.pid == 0:
            try:
                os.close(self.read_fd)
                os.close(self.stop_write)
                os.read(stop_read, 1)
            finally:
                os._exit(0)
        os.close(write_fd)
        os.close(stop_read)
        self.dropped = False
        return self.read_fd

    def drop(self) -> None:
        if self.dropped:
            return
        try:
            os.write(self.stop_write, b"x")
        except OSError:
            pass
        try:
            os.close(self.stop_write)
        except OSError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        self.dropped = True

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self.dropped:
            self.drop()


class PreparedCloneInventoryTests(unittest.TestCase):
    def test_plan_is_deterministic_and_does_not_generate_challenge(self):
        with mock.patch.object(
            INVENTORY,
            "new_controller_challenge",
            side_effect=AssertionError("plan generated entropy"),
        ):
            first = MODULE.build_plan(_inputs())
            second = MODULE.build_plan(_inputs())
        self.assertEqual(first, second)
        self.assertTrue(
            first["controller_challenge_generated_only_at_apply"]
        )
        self.assertFalse(first["collection_performed"])
        self.assertFalse(first["production_contacted"])

    def test_controller_plan_binds_all_sources_without_generating_nonce(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            context = _controller_context(root)
            with mock.patch.object(
                INVENTORY,
                "new_controller_challenge",
                side_effect=AssertionError("plan generated entropy"),
            ):
                first = MODULE.build_controller_plan(context)
                second = MODULE.build_controller_plan(context)
            self.assertEqual(first, second)
            self.assertEqual(first["mode"], "fresh-collect")
            self.assertEqual(
                first["release_closure_sha256"],
                context.release_closure_sha256,
            )
            self.assertEqual(
                first["release_artifact_git_blob"],
                context.release_artifact_git_blob,
            )
            self.assertEqual(first["runtime_authorization_checks"], 7)
            serialized = MODULE.canonical_json(first)
            self.assertNotIn(str(context.ssh_identity).encode(), serialized)
            self.assertNotIn(str(context.known_hosts).encode(), serialized)

    def test_controller_context_derives_exact_release_and_role_bindings(self):
        approval_payload = b'{"approval":"bound"}\n'
        policy_payload = b'{"policy":"bound"}\n'
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            evidence_root = root / "evidence"
            evidence_root.mkdir(mode=0o700)
            manifest, metadata = _controller_source_documents(
                approval_payload=approval_payload,
                policy_payload=policy_payload,
                evidence_root=evidence_root,
            )
            paths = {
                "manifest": root / "manifest.json",
                "approval": root / "approval.json",
                "policy": root / "policy.json",
                "closure": root / "release-closure.json",
                "prepare": root / "prepare.json",
                "identity": root / "identity",
                "known_hosts": root / "known-hosts",
            }
            for key, payload in (
                ("approval", approval_payload),
                ("policy", policy_payload),
                ("prepare", MODULE.canonical_json(metadata)),
                ("identity", b"private-key\n"),
                ("known_hosts", b"host-key\n"),
            ):
                paths[key].write_bytes(payload)
                paths[key].chmod(0o600)
            release_digests = {
                "inventory_agent": "1" * 64,
                "finland_precommit_worker": "2" * 64,
                "wa_ir_operation_worker": "4" * 64,
            }
            release_blobs = {
                "inventory_agent": "1" * 40,
                "finland_precommit_worker": "2" * 40,
                "wa_ir_operation_worker": "4" * 40,
            }
            with (
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "read_root_only_manifest",
                    return_value=(manifest, "8" * 64),
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "render_plan",
                    return_value={"plan_sha256": "9" * 64},
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "_verify_runtime_authorization",
                ) as authorize,
                mock.patch.object(
                    MODULE,
                    "_load_release_closure",
                    return_value="f" * 64,
                ),
                mock.patch.object(
                    MODULE,
                    "_verify_immutable_release_checkout",
                    return_value=(release_digests, release_blobs),
                ),
            ):
                context = MODULE.load_controller_context(
                    manifest_path=paths["manifest"],
                    approval_path=paths["approval"],
                    approval_policy_path=paths["policy"],
                    release_closure_path=paths["closure"],
                    prepare_metadata_path=paths["prepare"],
                    ssh_identity=paths["identity"],
                    known_hosts=paths["known_hosts"],
                )
            authorize.assert_called_once()
            self.assertEqual(context.output_root, evidence_root)
            self.assertEqual(
                context.collection_inputs.agent_sha256,
                release_digests["inventory_agent"],
            )
            self.assertEqual(
                context.collection_inputs.roles[
                    "bot_fi"
                ].contract_worker_sha256,
                release_digests["finland_precommit_worker"],
            )
            self.assertEqual(
                context.collection_inputs.roles[
                    "webapp_fi"
                ].contract_worker_sha256,
                release_digests["finland_precommit_worker"],
            )
            self.assertEqual(
                context.collection_inputs.roles[
                    "webapp_ir"
                ].contract_worker_sha256,
                release_digests["wa_ir_operation_worker"],
            )
            self.assertEqual(
                {
                    role: context.collection_inputs.roles[
                        role
                    ].role_manifest_sha256
                    for role in MODULE.ROLES
                },
                {
                    "bot_fi": "5" * 64,
                    "webapp_fi": "6" * 64,
                    "webapp_ir": "7" * 64,
                },
            )

    def test_prepare_runtime_target_descriptor_must_match_v3_manifest(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            manifest, metadata = _controller_source_documents(
                approval_payload=b'{"approval":"bound"}\n',
                policy_payload=b'{"policy":"bound"}\n',
                evidence_root=root / "evidence",
            )
            mismatched = copy.deepcopy(metadata)
            mismatched["controller_bindings"][
                "convergence_runtime_targets"
            ]["sha256"] = "e" * 64
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "runtime target descriptor",
            ):
                MODULE._validate_prepare_metadata(
                    mismatched,
                    manifest=manifest,
                )

            legacy = copy.deepcopy(metadata)
            legacy["schema"] = MODULE.PREPARE.LEGACY_SET_SCHEMA
            del legacy["capabilities"]
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
            "fresh v3 prepare material",
            ):
                MODULE._validate_prepare_metadata(
                    legacy,
                    manifest=manifest,
                )

            unsupported = copy.deepcopy(manifest)
            unsupported["schema"] = "unsupported-cutover-schema"
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "fresh v4 cutover manifest",
            ):
                MODULE._validate_prepare_metadata(
                    metadata,
                    manifest=unsupported,
                )

    def test_stopped_context_accepts_only_exact_historical_running_receipt(self):
        approval_payload = b'{"approval":"bound"}\n'
        policy_payload = b'{"policy":"bound"}\n'
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            evidence_root = root / "evidence"
            evidence_root.mkdir(mode=0o700)
            manifest, metadata = _controller_source_documents(
                approval_payload=approval_payload,
                policy_payload=policy_payload,
                evidence_root=evidence_root,
            )
            approval = root / "approval.json"
            policy = root / "policy.json"
            prepare = root / "prepare.json"
            identity = root / "identity"
            known_hosts = root / "known-hosts"
            for path, payload in (
                (approval, approval_payload),
                (policy, policy_payload),
                (prepare, MODULE.canonical_json(metadata)),
                (identity, b"private-key\n"),
                (known_hosts, b"host-key\n"),
            ):
                path.write_bytes(payload)
                path.chmod(0o600)
            running_inputs = _controller_inputs()
            requests = MODULE._request_set(
                running_inputs,
                challenge="d" * 64,
                issued_at=BASE,
                expires_at=BASE
                + timedelta(seconds=MODULE.REQUEST_LIFETIME_SECONDS),
            )
            responses, _command_times = _response_set(requests)
            prior_path = MODULE.canonical_receipt_path(
                evidence_root,
                operation_id=OPERATION_ID,
                controller_challenge_sha256="d" * 64,
            )
            loaded_prior = {
                "receipt": {
                    "expected_database_state": "running-healthy",
                    "aggregate_sha256": "e" * 64,
                },
                "requests": requests,
                "responses": responses,
            }
            patches = (
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "read_root_only_manifest",
                    return_value=(manifest, "8" * 64),
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "render_plan",
                    return_value={"plan_sha256": "9" * 64},
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "_verify_runtime_authorization",
                ),
                mock.patch.object(
                    MODULE,
                    "_load_release_closure",
                    return_value="f" * 64,
                ),
                mock.patch.object(
                    MODULE,
                    "_verify_immutable_release_checkout",
                    return_value=(
                        {
                            "inventory_agent": "1" * 64,
                            "finland_precommit_worker": "2" * 64,
                            "wa_ir_operation_worker": "4" * 64,
                        },
                        {
                            "inventory_agent": "1" * 40,
                            "finland_precommit_worker": "2" * 40,
                            "wa_ir_operation_worker": "4" * 40,
                        },
                    ),
                ),
                mock.patch.object(
                    MODULE,
                    "load_historical_running_prepared_clone_baseline_receipt",
                    return_value=loaded_prior,
                ),
            )
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                patches[4],
                patches[5] as load_prior,
            ):
                context = MODULE.load_controller_context(
                    manifest_path=root / "manifest.json",
                    approval_path=approval,
                    approval_policy_path=policy,
                    release_closure_path=root / "release-closure.json",
                    prepare_metadata_path=prepare,
                    ssh_identity=identity,
                    known_hosts=known_hosts,
                    expected_database_state="stopped",
                    prior_running_receipt=prior_path,
                    prior_running_challenge_sha256="d" * 64,
                    prior_running_receipt_sha256="c" * 64,
                )
            load_prior.assert_called_once_with(
                prior_path,
                output_root=evidence_root,
                expected_campaign_id=CAMPAIGN_ID,
                expected_operation_id=OPERATION_ID,
                expected_release_sha=RELEASE_SHA,
                expected_release_tree_sha=RELEASE_TREE_SHA,
                expected_controller_challenge_sha256="d" * 64,
                expected_aggregate_artifact_sha256="c" * 64,
            )
            self.assertEqual(
                context.collection_inputs.expected_database_state,
                "stopped",
            )
            self.assertEqual(
                context.collection_inputs.prior_requests,
                requests,
            )
            plan = MODULE.build_controller_plan(context)
            self.assertEqual(
                plan["prior_running_receipt"],
                context.prior_running_receipt,
            )
            self.assertIn(":stopped:", plan["collection_plan"][
                "required_confirmation"
            ])

    def test_stopped_context_rejects_missing_or_substituted_prior(self):
        running_inputs = _controller_inputs()
        with self.assertRaisesRegex(
            MODULE.PreparedCloneInventoryError,
            "requires one exact prior",
        ):
            MODULE._collection_inputs_for_state(
                running_inputs,
                output_root=Path("/tmp"),
                expected_database_state="stopped",
                prior_running_receipt=None,
                prior_running_challenge_sha256=None,
                prior_running_receipt_sha256=None,
            )

        requests = MODULE._request_set(
            running_inputs,
            challenge="d" * 64,
            issued_at=BASE,
            expires_at=BASE
            + timedelta(seconds=MODULE.REQUEST_LIFETIME_SECONDS),
        )
        substituted = copy.deepcopy(requests)
        substituted["bot_fi"] = requests["webapp_fi"]
        responses, _command_times = _response_set(requests)
        with (
            mock.patch.object(
                MODULE,
                "load_historical_running_prepared_clone_baseline_receipt",
                return_value={
                    "receipt": {
                        "expected_database_state": "running-healthy",
                        "aggregate_sha256": "e" * 64,
                    },
                    "requests": substituted,
                    "responses": responses,
                },
            ),
            self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "release binding differs",
            ),
        ):
            MODULE._collection_inputs_for_state(
                running_inputs,
                output_root=Path("/tmp"),
                expected_database_state="stopped",
                prior_running_receipt=Path(
                    "/tmp/prepared-clone-inventory/"
                    f"{OPERATION_ID}/{'d' * 64}/"
                    + MODULE.PRE_FREEZE_CURRENT_OPERATION_RECEIPT_FILENAME
                ),
                prior_running_challenge_sha256="d" * 64,
                prior_running_receipt_sha256="c" * 64,
            )

        with (
            mock.patch.object(
                MODULE,
                "load_historical_running_prepared_clone_baseline_receipt",
                side_effect=MODULE.PreparedCloneInventoryError(
                    "historical baseline binding differs"
                ),
            ),
            self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "prior running prepared inventory receipt is invalid",
            ),
        ):
            MODULE._collection_inputs_for_state(
                running_inputs,
                output_root=Path("/tmp"),
                expected_database_state="stopped",
                prior_running_receipt=Path(
                    "/tmp/prepared-clone-inventory/"
                    f"{OPERATION_ID}/{'d' * 64}/"
                    + MODULE.PRE_FREEZE_CURRENT_OPERATION_RECEIPT_FILENAME
                ),
                prior_running_challenge_sha256="d" * 64,
                prior_running_receipt_sha256="f" * 64,
            )

    def test_immutable_release_checkout_rejects_tampered_tracked_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "release"
            (root / "scripts").mkdir(parents=True)
            for index, relative in enumerate(
                MODULE.RELEASE_ARTIFACT_RELATIVE_PATHS.values()
            ):
                path = root / relative
                path.write_text(
                    f"#!/usr/bin/env python3\nVALUE = {index}\n",
                    encoding="ascii",
                )
                path.chmod(0o644)

            def git(*arguments: str) -> str:
                completed = subprocess.run(
                    ("git", "-C", str(root), *arguments),
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env={
                        **os.environ,
                        "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_CONFIG_GLOBAL": "/dev/null",
                    },
                )
                return completed.stdout.strip()

            git("init", "-q")
            git("add", ".")
            git(
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "release",
            )
            release_sha = git("rev-parse", "HEAD")
            release_tree_sha = git("rev-parse", "HEAD^{tree}")
            git("checkout", "-q", "--detach", "HEAD")
            digests, blobs = MODULE._verify_immutable_release_checkout(
                root,
                release_sha=release_sha,
                release_tree_sha=release_tree_sha,
            )
            self.assertEqual(set(digests), set(
                MODULE.RELEASE_ARTIFACT_RELATIVE_PATHS
            ))
            self.assertEqual(set(blobs), set(digests))
            target = (
                root
                / MODULE.RELEASE_ARTIFACT_RELATIVE_PATHS[
                    "inventory_agent"
                ]
            )
            target.write_text("tampered\n", encoding="ascii")
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "detached, clean",
            ):
                MODULE._verify_immutable_release_checkout(
                    root,
                    release_sha=release_sha,
                    release_tree_sha=release_tree_sha,
                )

    def test_aggregate_binds_exact_request_response_bytes_and_times(self):
        requests = _requests()
        responses, command_times = _response_set(requests)
        now = BASE + timedelta(seconds=8)
        aggregate = MODULE.build_aggregate(
            inputs=_inputs(),
            requests=requests,
            responses=responses,
            command_times=command_times,
            now=now,
        )
        validated = MODULE.validate_pre_freeze_current_operation_receipt(
            aggregate,
            requests=requests,
            responses=responses,
            now=now,
        )
        self.assertEqual(
            validated["schema"],
            MODULE.PRE_FREEZE_CURRENT_OPERATION_RECEIPT_SCHEMA,
        )
        for role in MODULE.ROLES:
            self.assertEqual(
                validated["roles"][role]["request_sha256"],
                hashlib.sha256(
                    MODULE.canonical_json(requests[role]) + b"\n"
                ).hexdigest(),
            )
            self.assertEqual(
                validated["roles"][role]["response_sha256"],
                hashlib.sha256(
                    MODULE.canonical_json(responses[role]) + b"\n"
                ).hexdigest(),
            )
            self.assertEqual(
                validated["roles"][role]["response_document_sha256"],
                responses[role]["response_sha256"],
            )
            self.assertEqual(
                validated["roles"][role][
                    "prepared_redis_chain_metadata_sha256"
                ],
                responses[role][
                    "prepared_redis_chain_metadata_sha256"
                ],
            )
            self.assertEqual(
                validated["roles"][role]["prepared_redis_target_count"],
                1,
            )
            self.assertEqual(
                validated["roles"][role][
                    "prepared_redis_unsafe_path_count"
                ],
                0,
            )
            self.assertEqual(
                validated["roles"][role]["prepared_redis_entry_count"],
                0,
            )
            self.assertTrue(
                validated["roles"][role]["prepared_redis_pristine"]
            )

    def test_replay_copied_or_mtime_touched_bytes_do_not_refresh_receipt(self):
        requests = _requests()
        responses, command_times = _response_set(requests)
        aggregate = MODULE.build_aggregate(
            inputs=_inputs(),
            requests=requests,
            responses=responses,
            command_times=command_times,
            now=BASE + timedelta(seconds=8),
        )
        copied = json.loads(MODULE.canonical_json(aggregate))
        with self.assertRaises(MODULE.PreparedCloneInventoryError):
            MODULE.validate_aggregate(
                copied,
                requests=requests,
                responses=responses,
                now=BASE + timedelta(seconds=91),
            )
        replacement = _requests(challenge="8" * 64)
        with self.assertRaises(MODULE.PreparedCloneInventoryError):
            MODULE.validate_aggregate(
                copied,
                requests=replacement,
                responses=responses,
                now=BASE + timedelta(seconds=8),
            )

    def test_cross_role_skew_and_challenge_substitution_fail_closed(self):
        requests = _requests()
        responses, command_times = _response_set(requests)
        late = BASE + timedelta(
            seconds=MODULE.MAX_ROLE_CAPTURE_SKEW_SECONDS + 2
        )
        responses["webapp_ir"] = _response(
            requests["webapp_ir"],
            captured_at=late,
            marker="c",
        )
        command_times["webapp_ir"] = (
            late - timedelta(seconds=1),
            late + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(
            MODULE.PreparedCloneInventoryError,
            "skew",
        ):
            MODULE.build_aggregate(
                inputs=_inputs(),
                requests=requests,
                responses=responses,
                command_times=command_times,
                now=late + timedelta(seconds=1),
            )

    def test_collect_generates_nonce_inside_apply_and_exact_three_roles(self):
        inputs = _inputs()
        plan = MODULE.build_plan(inputs)
        ticks = iter(
            BASE + timedelta(seconds=index)
            for index in range(20)
        )
        seen: list[str] = []
        invocations = 0
        authorization_checks = 0

        def authorize() -> None:
            nonlocal authorization_checks
            authorization_checks += 1

        def invoke(role: str, request: dict) -> MODULE.InvocationResult:
            nonlocal invocations
            seen.append(request["controller_challenge_sha256"])
            captured = BASE + timedelta(
                seconds=invocations * 2 + 1,
                microseconds=500_000,
            )
            marker = chr(ord("a") + invocations)
            invocations += 1
            payload = MODULE.canonical_json(
                _response(
                    request,
                    captured_at=captured,
                    marker=marker,
                )
            )
            return MODULE.InvocationResult(
                returncode=0,
                stdout=payload + b"\n",
                stderr=b"",
            )

        with (
            ExternalLiveness() as read_fd,
            mock.patch.object(
                INVENTORY,
                "new_controller_challenge",
                wraps=INVENTORY.new_controller_challenge,
            ) as generate,
        ):
            aggregate, requests, responses = MODULE.collect(
                inputs,
                invoke=invoke,
                confirm=plan["required_confirmation"],
                controller_liveness_fd=read_fd,
                authorization_check=authorize,
                clock=lambda: next(ticks),
            )
        generate.assert_called_once_with()
        self.assertEqual(authorization_checks, 5)
        self.assertEqual(set(requests), set(MODULE.ROLES))
        self.assertEqual(set(responses), set(MODULE.ROLES))
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(
            aggregate["controller_challenge_sha256"],
            seen[0],
        )

    def test_collect_requires_authorization_before_nonce_or_invocation(self):
        inputs = _inputs()
        plan = MODULE.build_plan(inputs)
        invoke = mock.Mock()
        with (
            ExternalLiveness() as read_fd,
            mock.patch.object(
                INVENTORY,
                "new_controller_challenge",
                side_effect=AssertionError("nonce generated"),
            ),
            self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "authorization is required",
            ),
        ):
            MODULE.collect(
                inputs,
                invoke=invoke,
                confirm=plan["required_confirmation"],
                controller_liveness_fd=read_fd,
                authorization_check=None,
                clock=lambda: BASE,
            )
        invoke.assert_not_called()

    def test_collect_rejects_noncanonical_response_wire(self):
        inputs = _inputs()
        plan = MODULE.build_plan(inputs)

        def invoke(_role: str, request: dict) -> MODULE.InvocationResult:
            response = _response(
                request,
                captured_at=BASE + timedelta(microseconds=500_000),
                marker="a",
            )
            payload = json.dumps(
                response,
                sort_keys=False,
                separators=(", ", ": "),
            ).encode("ascii")
            self.assertEqual(payload.count(b"\n"), 0)
            self.assertNotEqual(
                payload + b"\n",
                MODULE.canonical_json(response) + b"\n",
            )
            return MODULE.InvocationResult(
                returncode=0,
                stdout=payload + b"\n",
                stderr=b"",
            )

        ticks = iter((BASE, BASE, BASE + timedelta(seconds=1)))
        with (
            ExternalLiveness() as read_fd,
            self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "response is not canonical",
            ),
        ):
            MODULE.collect(
                inputs,
                invoke=invoke,
                confirm=plan["required_confirmation"],
                controller_liveness_fd=read_fd,
                authorization_check=lambda: None,
                clock=lambda: next(ticks),
            )

    def test_collect_rejects_naive_controller_clock(self):
        inputs = _inputs()
        plan = MODULE.build_plan(inputs)
        invoke = mock.Mock()
        with (
            ExternalLiveness() as read_fd,
            self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "collection issue time is invalid",
            ),
        ):
            MODULE.collect(
                inputs,
                invoke=invoke,
                confirm=plan["required_confirmation"],
                controller_liveness_fd=read_fd,
                authorization_check=lambda: None,
                clock=lambda: BASE.replace(tzinfo=None),
            )
        invoke.assert_not_called()

    def test_controller_apply_holds_authority_through_publish_and_resumes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            context = _controller_context(root)
            ticks = iter(
                BASE + timedelta(seconds=index)
                for index in range(10)
            )
            invocations = 0
            factory_arguments = {}

            def invoke(
                _role: str,
                request: dict,
            ) -> MODULE.InvocationResult:
                nonlocal invocations
                captured = BASE + timedelta(
                    seconds=invocations * 2 + 1,
                    microseconds=500_000,
                )
                marker = chr(ord("a") + invocations)
                invocations += 1
                response = _response(
                    request,
                    captured_at=captured,
                    marker=marker,
                )
                return MODULE.InvocationResult(
                    returncode=0,
                    stdout=MODULE.canonical_json(response) + b"\n",
                    stderr=b"",
                )

            def factory(**kwargs):
                factory_arguments.update(kwargs)
                return invoke

            plan = MODULE.build_controller_plan(context)
            with (
                ExternalLiveness() as read_fd,
                mock.patch.object(
                    MODULE,
                    "_assert_controller_sources_unchanged",
                ) as source_check,
                mock.patch.object(
                    MODULE,
                    "_runtime_authorization_check",
                ) as authorize,
                mock.patch.object(
                    INVENTORY,
                    "new_controller_challenge",
                    return_value="9" * 64,
                ),
            ):
                result = MODULE.execute_controller(
                    context,
                    apply=True,
                    confirm=plan["required_confirmation"],
                    controller_liveness_fd=read_fd,
                    invoker_factory=factory,
                    clock=lambda: next(ticks),
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["artifact_count"], 7)
            self.assertTrue(result["readback_verified"])
            self.assertEqual(authorize.call_count, 7)
            source_check.assert_called_once_with(context)
            self.assertEqual(
                factory_arguments,
                {
                    "ssh_identity": context.ssh_identity,
                    "ssh_identity_sha256": (
                        context.ssh_identity_sha256
                    ),
                    "known_hosts": context.known_hosts,
                    "known_hosts_sha256": context.known_hosts_sha256,
                },
            )

            receipt_path = Path(result["receipt_path"])
            resume_plan = MODULE.build_controller_plan(
                context,
                resume_receipt=receipt_path,
                resume_receipt_sha256=result["receipt_sha256"],
            )
            resume_factory = mock.Mock(
                side_effect=AssertionError("resume constructed invoker")
            )
            with (
                ExternalLiveness() as resume_fd,
                mock.patch.object(
                    MODULE,
                    "_assert_controller_sources_unchanged",
                ) as resume_source_check,
                mock.patch.object(
                    MODULE,
                    "_runtime_authorization_check",
                ) as resume_authorize,
            ):
                resumed = MODULE.execute_controller(
                    context,
                    apply=True,
                    confirm=resume_plan["required_confirmation"],
                    controller_liveness_fd=resume_fd,
                    resume_receipt=receipt_path,
                    resume_receipt_sha256=result["receipt_sha256"],
                    invoker_factory=resume_factory,
                    clock=lambda: BASE + timedelta(seconds=10),
                )
            self.assertEqual(
                resumed["status"],
                "resumed-readback-verified",
            )
            self.assertFalse(resumed["collection_performed"])
            self.assertFalse(resumed["production_contacted"])
            self.assertEqual(resumed["receipt"], result["receipt"])
            self.assertEqual(resume_authorize.call_count, 2)
            resume_source_check.assert_called_once_with(context)
            resume_factory.assert_not_called()

    def test_controller_rejects_expiry_between_collection_and_publication(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            context = _controller_context(root)
            ticks = iter(
                [
                    *(
                        BASE + timedelta(seconds=index)
                        for index in range(8)
                    ),
                    BASE + timedelta(minutes=5),
                ]
            )
            invocations = 0

            def invoke(
                _role: str,
                request: dict,
            ) -> MODULE.InvocationResult:
                nonlocal invocations
                response = _response(
                    request,
                    captured_at=BASE
                    + timedelta(
                        seconds=invocations * 2 + 1,
                        microseconds=500_000,
                    ),
                    marker=chr(ord("a") + invocations),
                )
                invocations += 1
                return MODULE.InvocationResult(
                    returncode=0,
                    stdout=MODULE.canonical_json(response) + b"\n",
                    stderr=b"",
                )

            plan = MODULE.build_controller_plan(context)
            with (
                ExternalLiveness() as read_fd,
                mock.patch.object(
                    MODULE,
                    "_assert_controller_sources_unchanged",
                ),
                mock.patch.object(
                    MODULE,
                    "_runtime_authorization_check",
                ) as authorize,
                mock.patch.object(
                    INVENTORY,
                    "new_controller_challenge",
                    return_value="9" * 64,
                ),
                self.assertRaisesRegex(
                    MODULE.PreparedCloneInventoryError,
                    "expired",
                ),
            ):
                MODULE.execute_controller(
                    context,
                    apply=True,
                    confirm=plan["required_confirmation"],
                    controller_liveness_fd=read_fd,
                    invoker_factory=lambda **_kwargs: invoke,
                    clock=lambda: next(ticks),
                )
            self.assertEqual(authorize.call_count, 6)
            self.assertFalse(
                (root / "prepared-clone-inventory").exists()
            )

    def test_controller_eof_after_collect_prevents_publication(self):
        requests = _requests()
        responses, command_times = _response_set(requests)
        aggregate = MODULE.build_aggregate(
            inputs=_inputs(),
            requests=requests,
            responses=responses,
            command_times=command_times,
            now=BASE + timedelta(seconds=8),
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            context = _controller_context(root)
            control = ExternalLiveness()
            publisher = mock.Mock()

            def collect_then_drop(*_args, **kwargs):
                control.drop()
                self.assertTrue(
                    kwargs["active_liveness"]._lost.wait(timeout=1.0)
                )
                return aggregate, requests, responses

            plan = MODULE.build_controller_plan(context)
            with (
                control as read_fd,
                mock.patch.object(
                    MODULE,
                    "_assert_controller_sources_unchanged",
                ),
                mock.patch.object(
                    MODULE,
                    "_runtime_authorization_check",
                ),
                mock.patch.object(
                    MODULE,
                    "collect",
                    side_effect=collect_then_drop,
                ),
                mock.patch.object(
                    MODULE,
                    "_publish_with_exact_reconciliation",
                    publisher,
                ),
                mock.patch.object(MODULE.os, "kill"),
                self.assertRaises(
                    MODULE.PreparedCloneInventoryCancellation
                ),
            ):
                MODULE.execute_controller(
                    context,
                    apply=True,
                    confirm=plan["required_confirmation"],
                    controller_liveness_fd=read_fd,
                    invoker_factory=lambda **_kwargs: mock.Mock(),
                    clock=lambda: BASE + timedelta(seconds=9),
                )
            publisher.assert_not_called()

    def test_aggregate_rejects_cross_role_source_substitution(self):
        requests = _requests()
        responses, command_times = _response_set(requests)
        swapped_requests = copy.deepcopy(requests)
        swapped_requests["bot_fi"] = requests["webapp_fi"]
        with self.assertRaises(MODULE.PreparedCloneInventoryError):
            MODULE.build_aggregate(
                inputs=_inputs(),
                requests=swapped_requests,
                responses=responses,
                command_times=command_times,
                now=BASE + timedelta(seconds=8),
            )

    def test_local_writer_and_preexisting_eof_are_rejected(self):
        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "retains a liveness writer",
            ):
                with MODULE.ControllerLiveness(read_fd):
                    self.fail("local writer was accepted")
        finally:
            for descriptor in (read_fd, write_fd):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "lost before collection",
            ):
                with MODULE.ControllerLiveness(read_fd):
                    self.fail("preexisting EOF was accepted")
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass

    def test_baseexception_preserved_and_signal_handlers_restored(self):
        inputs = _inputs()
        plan = MODULE.build_plan(inputs)
        before = {
            signum: signal.getsignal(signum)
            for signum in (
                signal.SIGINT,
                signal.SIGTERM,
                signal.SIGHUP,
                signal.SIGUSR1,
            )
        }
        with ExternalLiveness() as read_fd:
            with self.assertRaises(KeyboardInterrupt):
                MODULE.collect(
                    inputs,
                    invoke=lambda _role, _request: (_ for _ in ()).throw(
                        KeyboardInterrupt()
                    ),
                    confirm=plan["required_confirmation"],
                    controller_liveness_fd=read_fd,
                    authorization_check=lambda: None,
                    clock=lambda: BASE,
                )
        self.assertEqual(
            before,
            {
                signum: signal.getsignal(signum)
                for signum in before
            },
        )

    def test_receipt_publication_is_canonical_create_only_and_read_back(self):
        requests = _requests()
        responses, command_times = _response_set(requests)
        now = BASE + timedelta(seconds=8)
        aggregate = MODULE.build_aggregate(
            inputs=_inputs(),
            requests=requests,
            responses=responses,
            command_times=command_times,
            now=now,
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            publication = MODULE.publish_receipt_create_only(
                aggregate,
                requests=requests,
                responses=responses,
                output_root=root,
                now=now,
            )
            path = MODULE.canonical_receipt_path(
                root,
                operation_id=OPERATION_ID,
                controller_challenge_sha256="9" * 64,
            )
            self.assertEqual(publication["path"], str(path))
            self.assertTrue(publication["readback_verified"])
            self.assertEqual(publication["artifact_count"], 7)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            loaded = MODULE.load_pre_freeze_current_operation_receipt(
                path,
                output_root=root,
                now=now,
            )
            self.assertEqual(loaded["receipt"], aggregate)
            self.assertEqual(loaded["requests"], requests)
            self.assertEqual(loaded["responses"], responses)
            resumed = MODULE.publish_receipt_create_only(
                aggregate,
                requests=requests,
                responses=responses,
                output_root=root,
                now=now,
            )
            self.assertTrue(
                all(
                    resumed["artifacts"][role][kind]["reconciled"]
                    for role in MODULE.ROLES
                    for kind in ("request", "response")
                )
            )
            self.assertTrue(resumed["aggregate"]["reconciled"])

    def test_receipt_partial_publication_resumes_identical_bytes(self):
        requests = _requests()
        responses, command_times = _response_set(requests)
        now = BASE + timedelta(seconds=8)
        aggregate = MODULE.build_aggregate(
            inputs=_inputs(),
            requests=requests,
            responses=responses,
            command_times=command_times,
            now=now,
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            original = MODULE._publish_or_reconcile_artifact

            def crash_before_aggregate(directory_fd, filename, payload):
                if (
                    filename
                    == MODULE.PRE_FREEZE_CURRENT_OPERATION_RECEIPT_FILENAME
                ):
                    raise KeyboardInterrupt()
                return original(directory_fd, filename, payload)

            with (
                mock.patch.object(
                    MODULE,
                    "_publish_or_reconcile_artifact",
                    side_effect=crash_before_aggregate,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                MODULE.publish_receipt_create_only(
                    aggregate,
                    requests=requests,
                    responses=responses,
                    output_root=root,
                    now=now,
                )
            path = MODULE.canonical_receipt_path(
                root,
                operation_id=OPERATION_ID,
                controller_challenge_sha256="9" * 64,
            )
            self.assertFalse(path.exists())
            self.assertEqual(len(list(path.parent.iterdir())), 6)
            resumed = MODULE.publish_receipt_create_only(
                aggregate,
                requests=requests,
                responses=responses,
                output_root=root,
                now=now,
            )
            self.assertTrue(resumed["readback_verified"])
            self.assertTrue(resumed["aggregate"]["created"])

    def test_receipt_mismatch_symlink_and_hardlink_fail_closed(self):
        requests = _requests()
        responses, command_times = _response_set(requests)
        now = BASE + timedelta(seconds=8)
        aggregate = MODULE.build_aggregate(
            inputs=_inputs(),
            requests=requests,
            responses=responses,
            command_times=command_times,
            now=now,
        )

        def publish(root):
            publication = MODULE.publish_receipt_create_only(
                aggregate,
                requests=requests,
                responses=responses,
                output_root=root,
                now=now,
            )
            return Path(publication["path"])

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            path = publish(root)
            source = path.parent / MODULE.REQUEST_FILENAMES["bot_fi"]
            source.write_bytes(b"{}\n")
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "differs",
            ):
                publish(root)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            path = publish(root)
            source = path.parent / MODULE.REQUEST_FILENAMES["bot_fi"]
            response = path.parent / MODULE.RESPONSE_FILENAMES["bot_fi"]
            response.unlink()
            response.symlink_to(source.name)
            with self.assertRaises(MODULE.PreparedCloneInventoryError):
                MODULE.load_pre_freeze_current_operation_receipt(
                    path,
                    output_root=root,
                    now=now,
                )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            path = publish(root)
            source = path.parent / MODULE.REQUEST_FILENAMES["bot_fi"]
            response = path.parent / MODULE.RESPONSE_FILENAMES["bot_fi"]
            response.unlink()
            os.link(source, response)
            with self.assertRaises(MODULE.PreparedCloneInventoryError):
                MODULE.load_pre_freeze_current_operation_receipt(
                    path,
                    output_root=root,
                    now=now,
                )

    def test_receipt_loader_binds_root_chain_and_not_filesystem_mtime(self):
        requests = _requests()
        responses, command_times = _response_set(requests)
        now = BASE + timedelta(seconds=8)
        aggregate = MODULE.build_aggregate(
            inputs=_inputs(),
            requests=requests,
            responses=responses,
            command_times=command_times,
            now=now,
        )
        with tempfile.TemporaryDirectory() as raw:
            outer = Path(raw)
            root = outer / "expected-root"
            root.mkdir(mode=0o700)
            publication = MODULE.publish_receipt_create_only(
                aggregate,
                requests=requests,
                responses=responses,
                output_root=root,
                now=now,
            )
            path = Path(publication["path"])

            copied_root = outer / "copied-root"
            shutil.copytree(root, copied_root)
            copied_path = copied_root / path.relative_to(root)
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "expected output root",
            ):
                MODULE.load_pre_freeze_current_operation_receipt(
                    copied_path,
                    output_root=root,
                    now=now,
                )
            with self.assertRaises(MODULE.PreparedCloneInventoryError):
                MODULE.load_pre_freeze_current_operation_receipt(
                    copied_path,
                    output_root=copied_root,
                    now=BASE + timedelta(seconds=91),
                )

            os.utime(
                path,
                ns=(
                    path.stat().st_atime_ns,
                    path.stat().st_mtime_ns + 1,
                ),
            )
            with self.assertRaises(MODULE.PreparedCloneInventoryError):
                MODULE.load_pre_freeze_current_operation_receipt(
                    path,
                    output_root=root,
                    now=BASE + timedelta(seconds=91),
                )

            prepared_root = path.parent.parent.parent
            prepared_root.chmod(0o755)
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "not root-only",
            ):
                MODULE.load_pre_freeze_current_operation_receipt(
                    path,
                    output_root=root,
                    now=now,
                )
            prepared_root.chmod(0o700)

            challenge_directory = path.parent
            displaced = challenge_directory.with_name(
                challenge_directory.name + "-displaced"
            )
            challenge_directory.rename(displaced)
            challenge_directory.symlink_to(
                displaced.name,
                target_is_directory=True,
            )
            with self.assertRaises(
                MODULE.PreparedCloneInventoryError,
            ):
                MODULE.load_pre_freeze_current_operation_receipt(
                    path,
                    output_root=root,
                    now=now,
                )

    def test_historical_running_baseline_loader_is_explicit_and_bound(self):
        requests = _requests()
        responses, command_times = _response_set(requests)
        observed_at = BASE + timedelta(seconds=8)
        aggregate = MODULE.build_aggregate(
            inputs=_inputs(),
            requests=requests,
            responses=responses,
            command_times=command_times,
            now=observed_at,
        )
        with tempfile.TemporaryDirectory() as raw:
            outer = Path(raw)
            root = outer / "expected-root"
            root.mkdir(mode=0o700)
            publication = MODULE.publish_receipt_create_only(
                aggregate,
                requests=requests,
                responses=responses,
                output_root=root,
                now=observed_at,
            )
            path = Path(publication["path"])
            expired_now = BASE + timedelta(minutes=5)
            with self.assertRaises(MODULE.PreparedCloneInventoryError):
                MODULE.load_pre_freeze_current_operation_receipt(
                    path,
                    output_root=root,
                    now=expired_now,
                )

            historical = (
                MODULE.load_historical_running_prepared_clone_baseline_receipt(
                    path,
                    output_root=root,
                    expected_campaign_id=CAMPAIGN_ID,
                    expected_operation_id=OPERATION_ID,
                    expected_release_sha=RELEASE_SHA,
                    expected_release_tree_sha=RELEASE_TREE_SHA,
                    expected_controller_challenge_sha256="9" * 64,
                    expected_aggregate_artifact_sha256=publication[
                        "sha256"
                    ],
                    now=expired_now,
                )
            )
            self.assertEqual(
                historical["schema"],
                MODULE.HISTORICAL_BASELINE_LOADED_RECEIPT_SCHEMA,
            )
            self.assertEqual(
                historical["receipt"]["expected_database_state"],
                "running-healthy",
            )
            self.assertEqual(historical["requests"], requests)
            self.assertEqual(historical["responses"], responses)

            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "in the future",
            ):
                (
                    MODULE
                    .load_historical_running_prepared_clone_baseline_receipt(
                        path,
                        output_root=root,
                        expected_campaign_id=CAMPAIGN_ID,
                        expected_operation_id=OPERATION_ID,
                        expected_release_sha=RELEASE_SHA,
                        expected_release_tree_sha=RELEASE_TREE_SHA,
                        expected_controller_challenge_sha256="9" * 64,
                        expected_aggregate_artifact_sha256=publication[
                            "sha256"
                        ],
                        now=BASE + timedelta(seconds=2),
                    )
                )

            copied_root = outer / "copied-root"
            shutil.copytree(root, copied_root)
            copied_path = copied_root / path.relative_to(root)
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "expected output root",
            ):
                (
                    MODULE
                    .load_historical_running_prepared_clone_baseline_receipt(
                        copied_path,
                        output_root=root,
                        expected_campaign_id=CAMPAIGN_ID,
                        expected_operation_id=OPERATION_ID,
                        expected_release_sha=RELEASE_SHA,
                        expected_release_tree_sha=RELEASE_TREE_SHA,
                        expected_controller_challenge_sha256="9" * 64,
                        expected_aggregate_artifact_sha256=publication[
                            "sha256"
                        ],
                        now=expired_now,
                    )
                )

            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "binding differs",
            ):
                (
                    MODULE
                    .load_historical_running_prepared_clone_baseline_receipt(
                        path,
                        output_root=root,
                        expected_campaign_id=CAMPAIGN_ID,
                        expected_operation_id=OPERATION_ID,
                        expected_release_sha=RELEASE_SHA,
                        expected_release_tree_sha=RELEASE_TREE_SHA,
                        expected_controller_challenge_sha256="9" * 64,
                        expected_aggregate_artifact_sha256="f" * 64,
                        now=expired_now,
                    )
                )

    def test_historical_loader_rejects_stopped_receipt(self):
        running_requests = _requests()
        running_responses, _command_times = _response_set(
            running_requests
        )
        running_inputs = _inputs()
        stopped_inputs = MODULE.CollectionInputs(
            campaign_id=running_inputs.campaign_id,
            operation_id=running_inputs.operation_id,
            release_sha=running_inputs.release_sha,
            release_tree_sha=running_inputs.release_tree_sha,
            agent_sha256=running_inputs.agent_sha256,
            roles=running_inputs.roles,
            expected_database_state="stopped",
            prior_requests=running_requests,
            prior_responses=running_responses,
        )
        stopped_issued = BASE + timedelta(seconds=10)
        stopped_requests = MODULE._request_set(
            stopped_inputs,
            challenge="8" * 64,
            issued_at=stopped_issued,
            expires_at=stopped_issued
            + timedelta(seconds=MODULE.REQUEST_LIFETIME_SECONDS),
        )
        stopped_responses: dict[str, dict] = {}
        stopped_command_times = {}
        for index, role in enumerate(MODULE.ROLES):
            started = BASE + timedelta(seconds=index * 2 + 11)
            stopped_responses[role] = _response(
                stopped_requests[role],
                captured_at=started + timedelta(microseconds=500_000),
                marker=chr(ord("a") + index),
            )
            stopped_command_times[role] = (
                started,
                started + timedelta(seconds=1),
            )
        observed_at = BASE + timedelta(seconds=18)
        aggregate = MODULE.build_aggregate(
            inputs=stopped_inputs,
            requests=stopped_requests,
            responses=stopped_responses,
            command_times=stopped_command_times,
            now=observed_at,
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            root.chmod(0o700)
            publication = MODULE.publish_receipt_create_only(
                aggregate,
                requests=stopped_requests,
                responses=stopped_responses,
                output_root=root,
                now=observed_at,
            )
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "running baseline binding differs",
            ):
                (
                    MODULE
                    .load_historical_running_prepared_clone_baseline_receipt(
                        Path(publication["path"]),
                        output_root=root,
                        expected_campaign_id=CAMPAIGN_ID,
                        expected_operation_id=OPERATION_ID,
                        expected_release_sha=RELEASE_SHA,
                        expected_release_tree_sha=RELEASE_TREE_SHA,
                        expected_controller_challenge_sha256="8" * 64,
                        expected_aggregate_artifact_sha256=publication[
                            "sha256"
                        ],
                        now=BASE + timedelta(minutes=5),
                    )
                )

    def test_production_invoker_binds_canonical_topology_and_trust(self):
        requests = _requests()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            identity = root / "controller-identity"
            known_hosts = root / "known-hosts"
            identity_payload = b"private-controller-test-key\n"
            known_hosts_payload = b"canonical-host-key-entry\n"
            identity.write_bytes(identity_payload)
            known_hosts.write_bytes(known_hosts_payload)
            identity.chmod(0o600)
            known_hosts.chmod(0o600)
            invoker = MODULE.ProductionInvoker(
                ssh_identity=identity,
                ssh_identity_sha256=hashlib.sha256(
                    identity_payload
                ).hexdigest(),
                known_hosts=known_hosts,
                known_hosts_sha256=hashlib.sha256(
                    known_hosts_payload
                ).hexdigest(),
            )

            webapp_argv = invoker._argv(
                "webapp_fi",
                requests["webapp_fi"],
            )
            webapp_port = webapp_argv.index("-p")
            self.assertEqual(webapp_argv[webapp_port + 1], "37067")
            self.assertEqual(
                webapp_argv[-2],
                "root@65.109.220.59",
            )
            self.assertIn(
                f"UserKnownHostsFile={known_hosts}",
                webapp_argv,
            )
            self.assertIn(
                requests["webapp_fi"]["agent_path"],
                webapp_argv[-1],
            )

            wa_argv = invoker._argv(
                "webapp_ir",
                requests["webapp_ir"],
            )
            wa_port = wa_argv.index("-p")
            self.assertEqual(wa_argv[wa_port + 1], "22")
            self.assertEqual(wa_argv[-2], "root@95.38.164.29")

            local_argv = invoker._argv("bot_fi", requests["bot_fi"])
            self.assertEqual(local_argv[0], "/usr/bin/env")
            self.assertNotIn("/usr/bin/ssh", local_argv)

            identity.write_bytes(b"digest-substitution\n")
            with self.assertRaisesRegex(
                MODULE.PreparedCloneInventoryError,
                "digest differs",
            ):
                invoker._argv("webapp_fi", requests["webapp_fi"])


if __name__ == "__main__":
    unittest.main()
