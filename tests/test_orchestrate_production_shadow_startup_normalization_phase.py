from __future__ import annotations

import copy
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import orchestrate_production_shadow_prepared_clone_inventory as PREPARED
from scripts import orchestrate_production_shadow_startup_normalization_phase as MODULE
from scripts import production_shadow_cutover_controller as CONTROLLER
from scripts import production_shadow_global_docker_inventory_agent as INVENTORY
from scripts import production_shadow_startup_normalization_worker as WORKER
from tests import test_orchestrate_production_shadow_prepared_clone_inventory as FIXTURE
from tests import test_verify_production_shadow_phase_evidence as VERIFY_FIXTURE


BASE = FIXTURE.BASE


class WorkerBackend:
    def __init__(
        self,
        *,
        role: str,
        container_id: str,
        network_id: str,
    ) -> None:
        self.role = role
        self.container_id = container_id
        self.network_id = network_id
        self.calls: list[str] = []
        self.state_index = 0
        self.database_running = True

    def reconcile_prepared_database_running(self) -> WORKER.StartState:
        was_running = self.database_running
        self.database_running = True
        self.calls.append("start-reconcile")
        return WORKER.StartState(
            database_container_id=self.container_id,
            network_id=self.network_id,
            database_was_running=was_running,
            database_start_performed=not was_running,
            oneoff_residue_count=0,
        )

    def logical_state(self) -> WORKER.LogicalState:
        self.calls.append("state")
        self.state_index += 1
        marker = "before" if self.state_index == 1 else "normalized"
        return WORKER.LogicalState(
            database_fingerprint_sha256=hashlib.sha256(
                f"{self.role}-database-{marker}".encode("ascii")
            ).hexdigest(),
            database_row_count=7,
            database_table_count=3,
            uploads_tree_sha256=hashlib.sha256(
                f"{self.role}-uploads".encode("ascii")
            ).hexdigest(),
            audit_tree_sha256=hashlib.sha256(
                f"{self.role}-audit".encode("ascii")
            ).hexdigest(),
            redis_tree_sha256=hashlib.sha256(b"empty-redis").hexdigest(),
        )

    def normalize_once(self) -> dict:
        self.calls.append("normalize")
        return {
            "schema": (
                "production-shadow-startup-normalization-invocation-v1"
            ),
            "status": "normalized",
            "role": self.role,
            "background_jobs_enabled": False,
            "provider_credentials_present": False,
            "provider_network_used": False,
            "redis_started": False,
            "public_service_started": False,
        }

    def stop_operation_containers(self) -> WORKER.StopState:
        self.calls.append("stop")
        self.database_running = False
        return WORKER.StopState(
            database_container_id=self.container_id,
            network_id=self.network_id,
            operation_owned_running_container_count=0,
            oneoff_residue_count=0,
        )


class FakeLiveness:
    def __init__(self, _descriptor: int) -> None:
        self.check_count = 0

    def __enter__(self) -> "FakeLiveness":
        return self

    def check(self) -> None:
        self.check_count += 1

    def __exit__(self, _type, _value, _traceback) -> None:
        return None


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value
        self.lock = threading.Lock()

    def __call__(self) -> datetime:
        with self.lock:
            self.value += timedelta(seconds=1)
            return self.value

    def advance_to(self, value: datetime) -> None:
        with self.lock:
            if value > self.value:
                self.value = value


def _response_sha256(value: dict) -> None:
    value["response_sha256"] = hashlib.sha256(
        INVENTORY.canonical_json(
            {
                key: item
                for key, item in value.items()
                if key != "response_sha256"
            }
        )
    ).hexdigest()


def _source_set(
    *,
    stopped_issued_at: datetime = BASE + timedelta(seconds=40),
    shared_worker_challenge: bool = False,
    inputs: PREPARED.CollectionInputs | None = None,
) -> MODULE.ClosureInputs:
    inputs = FIXTURE._inputs() if inputs is None else inputs
    running_requests = PREPARED._request_set(
        inputs,
        challenge="9" * 64,
        issued_at=BASE,
        expires_at=BASE
        + timedelta(seconds=PREPARED.REQUEST_LIFETIME_SECONDS),
    )
    running_responses, running_times = FIXTURE._response_set(
        running_requests
    )
    running_now = BASE + timedelta(seconds=8)
    running_aggregate = PREPARED.build_aggregate(
        inputs=inputs,
        requests=running_requests,
        responses=running_responses,
        command_times=running_times,
        now=running_now,
    )

    normalization_requests: dict[str, dict] = {}
    normalization_results: dict[str, dict] = {}
    for index, role in enumerate(MODULE.ROLES):
        issued_at = BASE + timedelta(seconds=10 + index)
        challenge = (
            "d" * 64
            if shared_worker_challenge
            else chr(ord("d") + index) * 64
        )
        running_request = running_requests[role]
        request = WORKER.build_request(
            campaign_id=inputs.campaign_id,
            operation_id=inputs.operation_id,
            release_sha=inputs.release_sha,
            release_tree_sha=inputs.release_tree_sha,
            role=role,
            worker_sha256="f" * 64,
            inventory_agent_sha256=inputs.agent_sha256,
            contract_worker_sha256=inputs.roles[
                role
            ].contract_worker_sha256,
            role_manifest_path=running_request["role_manifest_path"],
            role_manifest_sha256=inputs.roles[
                role
            ].role_manifest_sha256,
            pre_inventory_request=running_request,
            pre_inventory_response=running_responses[role],
            controller_challenge_sha256=challenge,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(minutes=10),
        )
        backend = WorkerBackend(
            role=role,
            container_id=running_responses[role][
                "prepared_container_id"
            ],
            network_id=running_responses[role]["prepared_network_id"],
        )
        ticks = iter(
            (
                issued_at + timedelta(seconds=1),
                issued_at + timedelta(seconds=2),
            )
        )
        normalization_requests[role] = request
        normalization_results[role] = WORKER.execute(
            request,
            apply=True,
            confirm=WORKER.confirmation_phrase(request),
            authority=lambda _checkpoint: True,
            backend=backend,
            now=issued_at,
            clock=lambda: next(ticks),
        )

    stopped_inputs = PREPARED.CollectionInputs(
        campaign_id=inputs.campaign_id,
        operation_id=inputs.operation_id,
        release_sha=inputs.release_sha,
        release_tree_sha=inputs.release_tree_sha,
        agent_sha256=inputs.agent_sha256,
        roles=inputs.roles,
        expected_database_state="stopped",
        prior_requests=running_requests,
        prior_responses=running_responses,
    )
    stopped_requests = PREPARED._request_set(
        stopped_inputs,
        challenge="8" * 64,
        issued_at=stopped_issued_at,
        expires_at=stopped_issued_at
        + timedelta(seconds=PREPARED.REQUEST_LIFETIME_SECONDS),
    )
    stopped_responses: dict[str, dict] = {}
    stopped_times: dict[str, tuple[datetime, datetime]] = {}
    for index, role in enumerate(MODULE.ROLES):
        started = stopped_issued_at + timedelta(seconds=index * 2 + 1)
        captured = started + timedelta(microseconds=500_000)
        completed = started + timedelta(seconds=1)
        response = FIXTURE._response(
            stopped_requests[role],
            captured_at=captured,
            marker=chr(ord("a") + index),
        )
        response["prepared_database_running"] = False
        response["prepared_database_healthy"] = False
        _response_sha256(response)
        stopped_responses[role] = response
        stopped_times[role] = (started, completed)
    stopped_now = stopped_issued_at + timedelta(seconds=8)
    stopped_aggregate = PREPARED.build_aggregate(
        inputs=stopped_inputs,
        requests=stopped_requests,
        responses=stopped_responses,
        command_times=stopped_times,
        now=stopped_now,
    )
    return MODULE.ClosureInputs(
        running_aggregate=running_aggregate,
        running_requests=running_requests,
        running_responses=running_responses,
        normalization_requests=normalization_requests,
        normalization_results=normalization_results,
        stopped_aggregate=stopped_aggregate,
        stopped_requests=stopped_requests,
        stopped_responses=stopped_responses,
    )


def _semantic_inputs() -> PREPARED.CollectionInputs:
    manifest = VERIFY_FIXTURE.manifest_payload()
    return PREPARED.CollectionInputs(
        campaign_id=VERIFY_FIXTURE.CAMPAIGN_ID,
        operation_id=VERIFY_FIXTURE.OPERATION_ID,
        release_sha=VERIFY_FIXTURE.RELEASE_SHA,
        release_tree_sha=manifest["release_tree_sha"],
        agent_sha256="1" * 64,
        roles={
            role: PREPARED.RoleBinding(
                contract_worker_sha256=str(index + 2) * 64,
                role_manifest_sha256=str(index + 5) * 64,
            )
            for index, role in enumerate(PREPARED.ROLES)
        },
    )


def _semantic_closure() -> dict:
    return MODULE.validate_normalization_closure(
        _source_set(inputs=_semantic_inputs()),
        now=BASE + timedelta(seconds=48),
    )


def _evidence_context(
    root: Path,
) -> tuple[
    MODULE.EvidenceContext,
    CONTROLLER.ProductionCutoverJournal,
]:
    manifest = VERIFY_FIXTURE.manifest_payload()
    manifest["artifacts"] = copy.deepcopy(
        VERIFY_FIXTURE.MANIFEST_ARTIFACTS
    )
    CONTROLLER.validate_manifest(manifest)
    prior = VERIFY_FIXTURE.evidence_for(
        "pre_freeze_evidence",
        captured_at=BASE + timedelta(seconds=48),
    )
    prior_digest = MODULE._document_sha256(prior)
    journal = CONTROLLER.ProductionCutoverJournal(
        root / "journal.json",
        owner_uid=os.geteuid(),
    )
    with mock.patch.object(
        CONTROLLER,
        "_now",
        return_value=(BASE + timedelta(seconds=9)).isoformat(),
    ):
        journal.create(
            manifest_sha256=VERIFY_FIXTURE.MANIFEST_SHA256,
            plan_sha256=VERIFY_FIXTURE.PLAN_SHA256,
            campaign_id=manifest["campaign_id"],
            operation_id=manifest["operation_id"],
            release_sha=manifest["release_sha"],
            legacy_release_sha=manifest["legacy_release_sha"],
        )
        journal.begin_phase("pre_freeze_evidence")
        journal.complete_phase(
            "pre_freeze_evidence",
            verification=CONTROLLER.VerifiedPhaseCompletion(
                phase="pre_freeze_evidence",
                evidence_sha256=prior_digest,
                receipt_sha256="7" * 64,
            ),
        )
        state = journal.begin_phase(MODULE.PHASE)
    return (
        MODULE.EvidenceContext(
            manifest_path=root / "manifest.json",
            approval_path=root / "approval.json",
            approval_policy_path=root / "policy.json",
            journal_path=Path(
                manifest["deployment"]["controller_journal_path"]
            ),
            manifest=manifest,
            manifest_sha256=VERIFY_FIXTURE.MANIFEST_SHA256,
            plan={"plan_sha256": VERIFY_FIXTURE.PLAN_SHA256},
            plan_sha256=VERIFY_FIXTURE.PLAN_SHA256,
            journal=state,
            prior_records={"pre_freeze_evidence": prior},
            prior_digests={"pre_freeze_evidence": prior_digest},
            prior_paths={
                "pre_freeze_evidence": root / "pre-freeze.json"
            },
            output_root=root / "phase-output",
        ),
        journal,
    )


def _persisted_loader_fixture(
    root: Path,
) -> tuple[
    MODULE.EvidenceContext,
    MODULE.PersistedClosureSourceSpec,
    MODULE.ClosureInputs,
]:
    context, _journal = _evidence_context(root)
    output_root = context.output_root
    output_root.mkdir(mode=0o700)
    sources = _source_set(
        inputs=_semantic_inputs(),
        stopped_issued_at=BASE + timedelta(minutes=5),
    )
    running_publication = PREPARED.publish_receipt_create_only(
        sources.running_aggregate,
        requests=sources.running_requests,
        responses=sources.running_responses,
        output_root=output_root,
        now=BASE + timedelta(seconds=8),
    )
    stopped_publication = PREPARED.publish_receipt_create_only(
        sources.stopped_aggregate,
        requests=sources.stopped_requests,
        responses=sources.stopped_responses,
        output_root=output_root,
        now=BASE + timedelta(minutes=5, seconds=8),
    )
    request_paths: dict[str, Path] = {}
    request_digests: dict[str, str] = {}
    result_paths: dict[str, Path] = {}
    result_digests: dict[str, str] = {}
    source_root = output_root / MODULE.PHASE / "worker-sources"
    for role in MODULE.ROLES:
        request_path, request_digest = MODULE._persist_document(
            source_root,
            prefix=f"normalization-request-{role}",
            document=sources.normalization_requests[role],
        )
        result_path, result_digest = MODULE._persist_document(
            source_root,
            prefix=f"normalization-result-{role}",
            document=sources.normalization_results[role],
        )
        request_paths[role] = request_path
        request_digests[role] = request_digest
        result_paths[role] = result_path
        result_digests[role] = result_digest
    return (
        context,
        MODULE.PersistedClosureSourceSpec(
            running_receipt_path=Path(running_publication["path"]),
            running_controller_challenge_sha256=sources.running_aggregate[
                "controller_challenge_sha256"
            ],
            running_aggregate_artifact_sha256=running_publication[
                "sha256"
            ],
            stopped_receipt_path=Path(stopped_publication["path"]),
            stopped_controller_challenge_sha256=sources.stopped_aggregate[
                "controller_challenge_sha256"
            ],
            stopped_aggregate_artifact_sha256=stopped_publication[
                "sha256"
            ],
            normalization_request_paths=request_paths,
            normalization_request_artifact_sha256=request_digests,
            normalization_result_paths=result_paths,
            normalization_result_artifact_sha256=result_digests,
        ),
        sources,
    )


def _persist_source_spec_record(
    context: MODULE.EvidenceContext,
    spec: MODULE.PersistedClosureSourceSpec,
) -> Path:
    source, binding = MODULE._source_spec_binding(spec)
    record = {
        "schema": MODULE.SOURCE_SPEC_RECORD_SCHEMA,
        "status": "persisted-create-only-readback-verified",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "controller_plan_sha256": context.plan_sha256,
        "source_binding_sha256": binding,
        "source_spec": source,
        "parallel_worker_count": len(MODULE.ROLES),
        "worker_completion_skew_limit_seconds": (
            MODULE.MAX_WORKER_CAPTURE_SKEW_SECONDS
        ),
        "fresh_stopped_inventory": True,
        "journal_mutated": False,
        "production_contacted": True,
    }
    path, _digest = MODULE._persist_document(
        context.output_root / MODULE.PHASE / "source-spec",
        prefix="persisted-source-spec",
        document=record,
    )
    return path


class StartupNormalizationPhaseTests(unittest.TestCase):
    @staticmethod
    def _bare_worker_invoker() -> MODULE.ProductionWorkerInvoker:
        invoker = MODULE.ProductionWorkerInvoker.__new__(
            MODULE.ProductionWorkerInvoker
        )
        invoker._ownership_lock = threading.RLock()
        invoker._active_session_count = 0
        invoker._next_session_token = 1
        invoker._active_sessions = {}
        invoker._active_root_pids = {}
        invoker._active_roots = {}
        invoker._identity_owners = {}
        invoker._quarantined_identities = set()
        invoker._ownership_abort = threading.Event()
        invoker._direct_child_baseline = frozenset()
        invoker.session_factory = subprocess.Popen
        return invoker

    def test_fresh_closure_derives_only_three_zero_claims(self):
        sources = _source_set()
        observed = MODULE.validate_normalization_closure(
            sources,
            now=BASE + timedelta(seconds=48),
        )
        self.assertEqual(
            observed["claims"],
            {
                "legacy_resource_delta_count": 0,
                "operation_owned_running_container_count": 0,
                "unplanned_container_delta_count": 0,
            },
        )
        self.assertFalse(observed["caller_claims_accepted"])
        self.assertTrue(observed["stopped_inventory_freshly_validated"])
        for role in MODULE.ROLES:
            self.assertFalse(
                observed["roles"][role]["prepared_database_running"]
            )
            self.assertEqual(
                observed["roles"][role]["operation_resource_counts"],
                {
                    "container": 1,
                    "network": 1,
                    "volume": 0,
                    "image": 0,
                },
            )

    def test_historical_running_receipt_may_expire_before_stopped_capture(self):
        sources = _source_set(
            stopped_issued_at=BASE + timedelta(minutes=5)
        )
        observed = MODULE.validate_normalization_closure(
            sources,
            now=BASE + timedelta(minutes=5, seconds=8),
        )
        self.assertTrue(
            observed["running_inventory_historically_validated"]
        )

    def test_reordered_stopped_source_is_rejected(self):
        sources = _source_set(
            stopped_issued_at=BASE + timedelta(seconds=8)
        )
        with self.assertRaisesRegex(
            MODULE.StartupNormalizationPhaseError,
            "chronology",
        ):
            MODULE.validate_normalization_closure(
                sources,
                now=BASE + timedelta(seconds=17),
            )

    def test_cross_clock_edges_allow_five_seconds_but_reject_six(self):
        names = (
            "running_captured_at",
            "running_controller_observed_at",
            "normalization_issued_at",
            "normalization_captured_at",
            "normalization_completed_at",
            "stopped_issued_at",
            "stopped_captured_at",
            "stopped_controller_observed_at",
        )
        values = {
            name: BASE + timedelta(seconds=index * 10)
            for index, name in enumerate(names)
        }
        mutations = {
            "running-capture/controller-observation": (
                "running_captured_at",
                "running_controller_observed_at",
            ),
            "normalization-issue/remote-capture": (
                "normalization_issued_at",
                "normalization_captured_at",
            ),
            "remote-completion/stopped-issue": (
                "normalization_completed_at",
                "stopped_issued_at",
            ),
            "stopped-issue/remote-capture": (
                "stopped_issued_at",
                "stopped_captured_at",
            ),
            "remote-stopped-capture/controller-observation": (
                "stopped_captured_at",
                "stopped_controller_observed_at",
            ),
        }
        for label, (left, right) in mutations.items():
            with self.subTest(edge=label, skew="five"):
                candidate = dict(values)
                candidate[left] = candidate[right] + timedelta(seconds=5)
                self.assertTrue(MODULE._chronology_is_valid(**candidate))
            with self.subTest(edge=label, skew="six"):
                candidate = dict(values)
                candidate[left] = candidate[right] + timedelta(seconds=6)
                self.assertFalse(MODULE._chronology_is_valid(**candidate))

    def test_shared_worker_challenge_is_rejected(self):
        sources = _source_set(shared_worker_challenge=True)
        with self.assertRaisesRegex(
            MODULE.StartupNormalizationPhaseError,
            "challenges",
        ):
            MODULE.validate_normalization_closure(
                sources,
                now=BASE + timedelta(seconds=48),
            )

    def test_non_operation_root_change_is_rejected_by_stopped_contract(self):
        sources = _source_set()
        responses = copy.deepcopy(sources.stopped_responses)
        responses["webapp_ir"][
            "non_operation_inventory_root_sha256"
        ] = "7" * 64
        _response_sha256(responses["webapp_ir"])
        tampered = MODULE.ClosureInputs(
            running_aggregate=sources.running_aggregate,
            running_requests=sources.running_requests,
            running_responses=sources.running_responses,
            normalization_requests=sources.normalization_requests,
            normalization_results=sources.normalization_results,
            stopped_aggregate=sources.stopped_aggregate,
            stopped_requests=sources.stopped_requests,
            stopped_responses=responses,
        )
        with self.assertRaises(MODULE.StartupNormalizationPhaseError):
            MODULE.validate_normalization_closure(
                tampered,
                now=BASE + timedelta(seconds=48),
            )

    def test_public_closure_claim_tamper_fails_even_with_rehashed_document(self):
        observed = MODULE.validate_normalization_closure(
            _source_set(),
            now=BASE + timedelta(seconds=48),
        )
        observed["claims"]["legacy_resource_delta_count"] = 1
        observed["closure_sha256"] = MODULE._sha256(
            MODULE._canonical_json(
                {
                    key: item
                    for key, item in observed.items()
                    if key != "closure_sha256"
                }
            )
        )
        with self.assertRaisesRegex(
            MODULE.StartupNormalizationPhaseError,
            "safety claims",
        ):
            MODULE.validate_closure(observed)

    def test_plan_and_apply_gate_do_not_contact_production(self):
        plan = MODULE.execute(
            operation_id=FIXTURE.OPERATION_ID,
            release_sha=FIXTURE.RELEASE_SHA,
        )
        self.assertEqual(plan["status"], "planned")
        self.assertFalse(plan["production_contacted"])
        self.assertFalse(plan["journal_mutated"])
        self.assertFalse(plan["apply_supported"])
        with self.assertRaisesRegex(
            MODULE.StartupNormalizationPhaseError,
            "trusted persisted sources",
        ):
            MODULE.execute(
                operation_id=FIXTURE.OPERATION_ID,
                release_sha=FIXTURE.RELEASE_SHA,
                apply=True,
                confirm=plan["required_confirmation"],
            )

    def test_closure_is_canonical_json(self):
        observed = MODULE.validate_normalization_closure(
            _source_set(),
            now=BASE + timedelta(seconds=48),
        )
        payload = MODULE._canonical_json(observed)
        self.assertEqual(
            json.loads(payload.decode("ascii")),
            observed,
        )

    def test_publication_is_create_only_and_passes_release_verifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, journal = _evidence_context(root)
            closure = _semantic_closure()
            before = journal.load()
            with mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ):
                first = MODULE.publish_phase_evidence(
                    context,
                    closure=closure,
                    now=BASE + timedelta(seconds=49),
                )
                second = MODULE.publish_phase_evidence(
                    context,
                    closure=closure,
                    now=BASE + timedelta(seconds=49),
                )
            self.assertEqual(first, second)
            self.assertEqual(set(first), MODULE.PUBLICATION_FIELDS)
            self.assertFalse(first["production_contacted"])
            self.assertFalse(first["journal_mutated"])
            self.assertTrue(first["readback_verified"])
            self.assertEqual(journal.load(), before)
            evidence, digest = (
                MODULE.VERIFY.read_root_only_evidence(
                    Path(first["phase_evidence_path"])
                )
            )
            self.assertEqual(digest, first["phase_evidence_sha256"])
            self.assertEqual(evidence["claims"], {
                claim: {
                    "value": 0,
                    "source_sha256": first[
                        "claim_source_sha256"
                    ][claim],
                }
                for claim in MODULE.CLAIMS
            })
            for mapping_name in (
                "role_source_paths",
                "role_validation_paths",
                "claim_source_paths",
            ):
                for path in first[mapping_name].values():
                    self.assertEqual(
                        Path(path).stat().st_mode & 0o777,
                        0o600,
                    )

    def test_publication_rejects_non_started_journal_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, _journal = _evidence_context(root)
            forged = copy.deepcopy(dict(context.journal))
            forged["status"] = "active"
            bad_context = MODULE.EvidenceContext(
                manifest_path=context.manifest_path,
                approval_path=context.approval_path,
                approval_policy_path=context.approval_policy_path,
                journal_path=context.journal_path,
                manifest=context.manifest,
                manifest_sha256=context.manifest_sha256,
                plan=context.plan,
                plan_sha256=context.plan_sha256,
                journal=forged,
                prior_records=context.prior_records,
                prior_digests=context.prior_digests,
                prior_paths=context.prior_paths,
                output_root=context.output_root,
            )
            with self.assertRaisesRegex(
                MODULE.StartupNormalizationPhaseError,
                "controller context",
            ):
                MODULE.publish_phase_evidence(
                    bad_context,
                    closure=_semantic_closure(),
                    now=BASE + timedelta(seconds=49),
                )
            self.assertFalse(context.output_root.exists())

    def test_publication_rejects_manifest_closure_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, _journal = _evidence_context(root)
            closure = _semantic_closure()
            closure["release_tree_sha"] = "d" * 40
            closure["closure_sha256"] = MODULE._sha256(
                MODULE._canonical_json(
                    {
                        key: item
                        for key, item in closure.items()
                        if key != "closure_sha256"
                    }
                )
            )
            with self.assertRaisesRegex(
                MODULE.StartupNormalizationPhaseError,
                "cutover manifest",
            ), mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ):
                MODULE.publish_phase_evidence(
                    context,
                    closure=closure,
                    now=BASE + timedelta(seconds=49),
                )
            self.assertFalse(context.output_root.exists())

    def test_publication_rejects_caller_selected_output_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, _journal = _evidence_context(root)
            with self.assertRaisesRegex(
                MODULE.StartupNormalizationPhaseError,
                "manifest-derived",
            ):
                MODULE.publish_phase_evidence(
                    context,
                    closure=_semantic_closure(),
                    now=BASE + timedelta(seconds=49),
                )

    def test_persisted_loader_uses_historical_running_and_fresh_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, spec, expected = _persisted_loader_fixture(root)
            with mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ):
                observed = MODULE.PersistedClosureSourceLoader(
                    context,
                    spec,
                    now=BASE + timedelta(minutes=5, seconds=9),
                ).load()
            self.assertEqual(observed, expected)
            closure = MODULE.validate_normalization_closure(
                observed,
                now=BASE + timedelta(minutes=5, seconds=9),
            )
            self.assertTrue(
                closure["running_inventory_historically_validated"]
            )
            self.assertTrue(closure["stopped_inventory_freshly_validated"])

    def test_persisted_loader_rejects_copied_root_and_copied_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, spec, _expected = _persisted_loader_fixture(root)
            copied_root = root / "copied-output"
            shutil.copytree(context.output_root, copied_root)

            def copied(path: Path) -> Path:
                return copied_root / path.relative_to(context.output_root)

            copied_spec = MODULE.PersistedClosureSourceSpec(
                running_receipt_path=copied(spec.running_receipt_path),
                running_controller_challenge_sha256=(
                    spec.running_controller_challenge_sha256
                ),
                running_aggregate_artifact_sha256=(
                    spec.running_aggregate_artifact_sha256
                ),
                stopped_receipt_path=copied(spec.stopped_receipt_path),
                stopped_controller_challenge_sha256=(
                    spec.stopped_controller_challenge_sha256
                ),
                stopped_aggregate_artifact_sha256=(
                    spec.stopped_aggregate_artifact_sha256
                ),
                normalization_request_paths={
                    role: copied(path)
                    for role, path in (
                        spec.normalization_request_paths.items()
                    )
                },
                normalization_request_artifact_sha256=(
                    spec.normalization_request_artifact_sha256
                ),
                normalization_result_paths={
                    role: copied(path)
                    for role, path in (
                        spec.normalization_result_paths.items()
                    )
                },
                normalization_result_artifact_sha256=(
                    spec.normalization_result_artifact_sha256
                ),
            )
            with mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ), self.assertRaisesRegex(
                MODULE.StartupNormalizationPhaseError,
                "persisted prepared inventory",
            ):
                MODULE.PersistedClosureSourceLoader(
                    context,
                    copied_spec,
                    now=BASE + timedelta(minutes=5, seconds=9),
                ).load()

            copied_context = MODULE.EvidenceContext(
                manifest_path=context.manifest_path,
                approval_path=context.approval_path,
                approval_policy_path=context.approval_policy_path,
                journal_path=context.journal_path,
                manifest=context.manifest,
                manifest_sha256=context.manifest_sha256,
                plan=context.plan,
                plan_sha256=context.plan_sha256,
                journal=context.journal,
                prior_records=context.prior_records,
                prior_digests=context.prior_digests,
                prior_paths=context.prior_paths,
                output_root=copied_root,
            )
            with mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ), self.assertRaisesRegex(
                MODULE.StartupNormalizationPhaseError,
                "manifest-derived",
            ):
                MODULE.PersistedClosureSourceLoader(
                    copied_context,
                    copied_spec,
                    now=BASE + timedelta(minutes=5, seconds=9),
                ).load()

    def test_apply_persisted_phase_verifies_and_completes_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, spec, _sources = _persisted_loader_fixture(root)
            actual_journal = CONTROLLER.ProductionCutoverJournal(
                root / "journal.json",
                owner_uid=os.geteuid(),
            )
            authorization_calls: list[str] = []

            def authorize(observed: MODULE.EvidenceContext) -> None:
                authorization_calls.append(
                    observed.journal["status"]
                )

            def verifier(**arguments):
                evidence_path = Path(arguments["evidence_path"])
                verification_paths = tuple(
                    (
                        evidence_path.parents[1]
                        / "local-verification"
                    ).glob("local-verification.*.json")
                )
                self.assertEqual(len(verification_paths), 1)
                document = json.loads(
                    verification_paths[0].read_text(encoding="ascii")
                )
                return CONTROLLER._validate_phase_verification_result(
                    document,
                    phase=MODULE.PHASE,
                    manifest=arguments["manifest"],
                    manifest_sha256=arguments["manifest_sha256"],
                    plan_sha256=arguments["plan"]["plan_sha256"],
                )

            def persist_receipt(*, token, receipt, evidence_root):
                del evidence_root
                return CONTROLLER._persist_phase_verification_receipt(
                    token=token,
                    receipt=receipt,
                    evidence_root=root / "controller-evidence",
                )

            _source, binding = MODULE._source_spec_binding(spec)
            plan = MODULE.build_plan(
                operation_id=context.manifest["operation_id"],
                release_sha=context.manifest["release_sha"],
                source_loader_available=True,
                manifest_sha256=context.manifest_sha256,
                controller_plan_sha256=context.plan_sha256,
                source_binding_sha256=binding,
            )
            with mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ):
                result = MODULE.apply_persisted_phase(
                    context,
                    source_spec=spec,
                    confirm=plan["required_confirmation"],
                    control_fd=99,
                    now=BASE + timedelta(minutes=5, seconds=9),
                    journal_factory=lambda _path: actual_journal,
                    liveness_factory=FakeLiveness,
                    signal_authority_factory=lambda: nullcontext(),
                    authorization_verifier=authorize,
                    release_verifier=verifier,
                    receipt_persister=persist_receipt,
                )
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["journal_mutated"])
            self.assertFalse(result["production_contacted"])
            self.assertGreaterEqual(len(authorization_calls), 4)
            state = actual_journal.load()
            self.assertEqual(
                state["completed_phases"],
                ["pre_freeze_evidence", MODULE.PHASE],
            )
            self.assertEqual(
                state["phase_evidence_sha256"][MODULE.PHASE],
                result["phase_evidence_sha256"],
            )

    def test_apply_requires_prestarted_phase_without_journal_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, spec, _sources = _persisted_loader_fixture(root)
            journal_path = root / "active-journal.json"
            journal = CONTROLLER.ProductionCutoverJournal(
                journal_path,
                owner_uid=os.geteuid(),
            )
            with mock.patch.object(
                CONTROLLER,
                "_now",
                return_value=(BASE + timedelta(seconds=9)).isoformat(),
            ):
                journal.create(
                    manifest_sha256=context.manifest_sha256,
                    plan_sha256=context.plan_sha256,
                    campaign_id=context.manifest["campaign_id"],
                    operation_id=context.manifest["operation_id"],
                    release_sha=context.manifest["release_sha"],
                    legacy_release_sha=context.manifest[
                        "legacy_release_sha"
                    ],
                )
                journal.begin_phase("pre_freeze_evidence")
                journal.complete_phase(
                    "pre_freeze_evidence",
                    verification=CONTROLLER.VerifiedPhaseCompletion(
                        phase="pre_freeze_evidence",
                        evidence_sha256=context.prior_digests[
                            "pre_freeze_evidence"
                        ],
                        receipt_sha256="7" * 64,
                    ),
                )
            ready_context = replace(
                context,
                journal=journal.load(),
            )
            _source, binding = MODULE._source_spec_binding(spec)
            plan = MODULE.build_plan(
                operation_id=context.manifest["operation_id"],
                release_sha=context.manifest["release_sha"],
                source_loader_available=True,
                manifest_sha256=context.manifest_sha256,
                controller_plan_sha256=context.plan_sha256,
                source_binding_sha256=binding,
            )
            before = journal_path.read_bytes()
            with (
                mock.patch.object(
                    MODULE,
                    "_manifest_output_root",
                    return_value=context.output_root,
                ),
                self.assertRaisesRegex(
                    MODULE.StartupNormalizationPhaseError,
                    "exact durable journal successor",
                ),
            ):
                MODULE.apply_persisted_phase(
                    ready_context,
                    source_spec=spec,
                    confirm=plan["required_confirmation"],
                    control_fd=99,
                    journal_factory=lambda _path: journal,
                    liveness_factory=lambda _fd: self.fail(
                        "unstarted apply entered liveness"
                    ),
                    signal_authority_factory=lambda: self.fail(
                        "unstarted apply entered signal authority"
                    ),
                    authorization_verifier=lambda _context: self.fail(
                        "unstarted apply reauthorized"
                    ),
                    release_verifier=lambda **_kwargs: self.fail(
                        "unstarted apply ran verifier"
                    ),
                    receipt_persister=lambda **_kwargs: self.fail(
                        "unstarted apply persisted receipt"
                    ),
                )
            self.assertEqual(journal_path.read_bytes(), before)
            state = journal.load()
            self.assertEqual(state["status"], "active")
            self.assertIsNone(state["started_phase"])
            self.assertEqual(
                state["completed_phases"],
                ["pre_freeze_evidence"],
            )

    def test_completed_resume_does_not_reauthorize_or_reload_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, spec, _sources = _persisted_loader_fixture(root)
            actual_journal = CONTROLLER.ProductionCutoverJournal(
                root / "journal.json",
                owner_uid=os.geteuid(),
            )
            completion = CONTROLLER.VerifiedPhaseCompletion(
                phase=MODULE.PHASE,
                evidence_sha256="8" * 64,
                receipt_sha256="9" * 64,
            )
            actual_journal.complete_phase(
                MODULE.PHASE,
                verification=completion,
            )
            completed_context = replace(
                context,
                journal=actual_journal.load(),
            )
            _source, binding = MODULE._source_spec_binding(spec)
            plan = MODULE.build_plan(
                operation_id=context.manifest["operation_id"],
                release_sha=context.manifest["release_sha"],
                source_loader_available=True,
                manifest_sha256=context.manifest_sha256,
                controller_plan_sha256=context.plan_sha256,
                source_binding_sha256=binding,
            )
            with mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ):
                result = MODULE.apply_persisted_phase(
                    completed_context,
                    source_spec=spec,
                    confirm=plan["required_confirmation"],
                    control_fd=99,
                    journal_factory=lambda _path: actual_journal,
                    liveness_factory=lambda _fd: self.fail(
                        "completed resume entered liveness"
                    ),
                    signal_authority_factory=lambda: self.fail(
                        "completed resume entered signal authority"
                    ),
                    authorization_verifier=lambda _context: self.fail(
                        "completed resume reauthorized"
                    ),
                    release_verifier=lambda **_kwargs: self.fail(
                        "completed resume reran verifier"
                    ),
                    receipt_persister=lambda **_kwargs: self.fail(
                        "completed resume rewrote receipt"
                    ),
                    completed_reader=lambda _context, **_kwargs: {
                        "status": "completed-reused",
                        "phase_evidence_path": "/verified/evidence.json",
                        "phase_evidence_sha256": completion.evidence_sha256,
                        "verification_receipt_path": (
                            "/verified/receipt.json"
                        ),
                        "verification_receipt_sha256": (
                            completion.receipt_sha256
                        ),
                    },
                )
            self.assertEqual(result["status"], "completed-reused")
            self.assertFalse(result["journal_mutated"])
            self.assertFalse(result["production_contacted"])

    def test_source_producer_starts_three_workers_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, actual_journal = _evidence_context(root)
            context.output_root.mkdir(mode=0o700)
            sources = _source_set(
                inputs=_semantic_inputs(),
                stopped_issued_at=BASE + timedelta(minutes=5),
            )
            running_publication = PREPARED.publish_receipt_create_only(
                sources.running_aggregate,
                requests=sources.running_requests,
                responses=sources.running_responses,
                output_root=context.output_root,
                now=BASE + timedelta(seconds=8),
            )
            baseline = MODULE.RunningBaselineSpec(
                receipt_path=Path(running_publication["path"]),
                controller_challenge_sha256=sources.running_aggregate[
                    "controller_challenge_sha256"
                ],
                aggregate_artifact_sha256=running_publication["sha256"],
            )
            worker_sha256 = "f" * 64
            clock = MutableClock(BASE + timedelta(minutes=5))
            starts: dict[str, float] = {}
            starts_lock = threading.Lock()

            def worker_invoker(
                role,
                request,
                *,
                authority_check,
                cancellation,
            ):
                with starts_lock:
                    starts[role] = time.monotonic()

                def authority(checkpoint):
                    authority_check(role, checkpoint)
                    return not cancellation.is_set()

                issued_at = datetime.fromisoformat(
                    request["issued_at"][:-1] + "+00:00"
                )
                ticks = iter(
                    (
                        issued_at + timedelta(seconds=1),
                        issued_at + timedelta(seconds=2),
                    )
                )
                return WORKER.execute(
                    request,
                    apply=True,
                    confirm=WORKER.confirmation_phrase(request),
                    authority=authority,
                    backend=WorkerBackend(
                        role=role,
                        container_id=request[
                            "pre_inventory_response"
                        ]["prepared_container_id"],
                        network_id=request[
                            "pre_inventory_response"
                        ]["prepared_network_id"],
                    ),
                    now=issued_at,
                    clock=lambda: next(ticks),
                )

            def stopped_collector(
                inputs,
                *,
                invoke,
                confirm,
                controller_liveness_fd,
                authorization_check,
                clock: MutableClock,
            ):
                del invoke
                try:
                    self.assertEqual(
                        confirm,
                        PREPARED.build_plan(inputs)[
                            "required_confirmation"
                        ],
                    )
                    authorization_check()
                    issued_at = clock()
                    requests = PREPARED._request_set(
                        inputs,
                        challenge="7" * 64,
                        issued_at=issued_at,
                        expires_at=issued_at
                        + timedelta(
                            seconds=PREPARED.REQUEST_LIFETIME_SECONDS
                        ),
                    )
                    responses: dict[str, dict] = {}
                    command_times = {}
                    for index, role in enumerate(MODULE.ROLES):
                        started = issued_at + timedelta(
                            seconds=index * 2 + 1
                        )
                        captured = started + timedelta(
                            microseconds=500_000
                        )
                        completed = started + timedelta(seconds=1)
                        response = FIXTURE._response(
                            requests[role],
                            captured_at=captured,
                            marker=chr(ord("a") + index),
                        )
                        response["prepared_database_running"] = False
                        response["prepared_database_healthy"] = False
                        _response_sha256(response)
                        responses[role] = response
                        command_times[role] = (started, completed)
                    completed_at = issued_at + timedelta(seconds=8)
                    clock.advance_to(completed_at)
                    aggregate = PREPARED.build_aggregate(
                        inputs=inputs,
                        requests=requests,
                        responses=responses,
                        command_times=command_times,
                        now=completed_at,
                    )
                    authorization_check()
                    return aggregate, requests, responses
                finally:
                    os.close(controller_liveness_fd)

            with mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ):
                plan = MODULE.build_source_production_plan(
                    context,
                    baseline=baseline,
                    worker_sha256=worker_sha256,
                )
                read_fd, write_fd = os.pipe()
                try:
                    spec, result = MODULE.produce_persisted_sources(
                        context,
                        baseline=baseline,
                        confirm=plan["required_confirmation"],
                        control_fd=read_fd,
                        worker_invoker=worker_invoker,
                        stopped_inventory_invoker=lambda *_args: None,
                        now=BASE + timedelta(minutes=5),
                        clock=clock,
                        journal_factory=lambda _path: actual_journal,
                        liveness_factory=FakeLiveness,
                        signal_authority_factory=lambda: nullcontext(),
                        authorization_verifier=lambda _context: None,
                        stopped_collector=stopped_collector,
                        worker_artifact_verifier=lambda _running: (
                            worker_sha256
                        ),
                    )
                finally:
                    os.close(read_fd)
                    os.close(write_fd)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["production_contacted"])
            self.assertFalse(result["journal_mutated"])
            self.assertEqual(set(starts), set(MODULE.ROLES))
            self.assertLess(max(starts.values()) - min(starts.values()), 0.2)
            self.assertIsInstance(
                spec,
                MODULE.PersistedClosureSourceSpec,
            )
            for path in (
                *spec.normalization_request_paths.values(),
                *spec.normalization_result_paths.values(),
            ):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_phase_request_loader_binds_digest_path_and_source_record(self):
        with tempfile.TemporaryDirectory() as directory:
            context, spec, _sources = _persisted_loader_fixture(
                Path(directory)
            )
            with mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ):
                source_path = _persist_source_spec_record(context, spec)
                publication = MODULE.persist_phase_request_create_only(
                    context,
                    source_spec_record_path=source_path,
                )
                with mock.patch.object(
                    MODULE,
                    "load_evidence_context",
                    return_value=context,
                ):
                    loaded_context, loaded_spec, request_sha256 = (
                        MODULE.load_phase_request(
                            Path(publication["path"])
                        )
                    )
            self.assertEqual(loaded_context, context)
            self.assertEqual(loaded_spec, spec)
            self.assertEqual(
                request_sha256,
                publication["sha256"],
            )
            self.assertEqual(
                Path(publication["path"]).stat().st_mode & 0o777,
                0o600,
            )

    def test_phase_request_rejects_bad_mode_and_substituted_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context, spec, _sources = _persisted_loader_fixture(root)
            with mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ):
                source_path = _persist_source_spec_record(context, spec)
                publication = MODULE.persist_phase_request_create_only(
                    context,
                    source_spec_record_path=source_path,
                )
                request_path = Path(publication["path"])
                document = json.loads(
                    request_path.read_text(encoding="ascii")
                )
                document["mode"] = "caller-selected-mode"
                bad_mode_path, _digest = MODULE._persist_document(
                    request_path.parent,
                    prefix="phase-request",
                    document=document,
                )
                copied = root / "copied-phase-request.json"
                shutil.copyfile(request_path, copied)
                copied.chmod(0o600)
                with mock.patch.object(
                    MODULE,
                    "load_evidence_context",
                    return_value=context,
                ):
                    with self.assertRaisesRegex(
                        MODULE.StartupNormalizationPhaseError,
                        "fields differ",
                    ):
                        MODULE.load_phase_request(bad_mode_path)
                    with self.assertRaisesRegex(
                        MODULE.StartupNormalizationPhaseError,
                        "path/digest",
                    ):
                        MODULE.load_phase_request(copied)
                    request_path.chmod(0o644)
                    with self.assertRaisesRegex(
                        MODULE.StartupNormalizationPhaseError,
                        "unavailable or invalid",
                    ):
                        MODULE.load_phase_request(request_path)

    def test_real_cli_request_plan_and_apply_wiring(self):
        with tempfile.TemporaryDirectory() as directory:
            context, spec, _sources = _persisted_loader_fixture(
                Path(directory)
            )
            with mock.patch.object(
                MODULE,
                "_manifest_output_root",
                return_value=context.output_root,
            ):
                source_path = _persist_source_spec_record(context, spec)
                publication = MODULE.persist_phase_request_create_only(
                    context,
                    source_spec_record_path=source_path,
                )
                request_path = Path(publication["path"])
                output = SimpleNamespace(buffer=io.BytesIO())
                with (
                    mock.patch.object(
                        MODULE,
                        "load_evidence_context",
                        return_value=context,
                    ),
                    mock.patch.object(MODULE.sys, "stdout", output),
                ):
                    status = MODULE.main(
                        ["--request", str(request_path)]
                    )
                self.assertEqual(status, 0)
                planned = json.loads(output.buffer.getvalue())
                self.assertEqual(planned["status"], "planned")
                self.assertTrue(planned["apply_supported"])

                _source, binding = MODULE._source_spec_binding(spec)
                plan = MODULE.build_plan(
                    operation_id=context.manifest["operation_id"],
                    release_sha=context.manifest["release_sha"],
                    source_loader_available=True,
                    manifest_sha256=context.manifest_sha256,
                    controller_plan_sha256=context.plan_sha256,
                    source_binding_sha256=binding,
                )
                output = SimpleNamespace(buffer=io.BytesIO())
                with (
                    mock.patch.object(
                        MODULE,
                        "load_evidence_context",
                        return_value=context,
                    ),
                    mock.patch.object(
                        MODULE,
                        "apply_persisted_phase",
                        return_value={
                            "schema": MODULE.RESULT_SCHEMA,
                            "status": "completed",
                            "production_contacted": False,
                            "journal_mutated": True,
                        },
                    ) as apply_phase,
                    mock.patch.object(MODULE.sys, "stdout", output),
                ):
                    status = MODULE.main(
                        [
                            "--request",
                            str(request_path),
                            "--apply",
                            "--confirm",
                            plan["required_confirmation"],
                            "--controller-liveness-fd",
                            "17",
                        ]
                    )
                self.assertEqual(status, 0)
                completed = json.loads(output.buffer.getvalue())
                self.assertEqual(completed["status"], "completed")
                self.assertEqual(
                    apply_phase.call_args.kwargs["control_fd"],
                    17,
                )

    def test_cli_bad_request_path_is_generic_and_traceback_free(self):
        output = SimpleNamespace(buffer=io.BytesIO())
        secret_path = "/root/never-disclose-this-request.json"
        with mock.patch.object(MODULE.sys, "stdout", output):
            status = MODULE.main(["--request", secret_path])
        self.assertEqual(status, 1)
        payload = output.buffer.getvalue()
        self.assertNotIn(secret_path.encode("ascii"), payload)
        self.assertNotIn(b"Traceback", payload)
        self.assertEqual(json.loads(payload)["status"], "blocked")

    def test_one_role_lost_result_then_all_role_retry_is_safe(self):
        sources = _source_set(
            inputs=_semantic_inputs(),
            stopped_issued_at=BASE + timedelta(minutes=5),
        )
        issued_at = BASE + timedelta(minutes=5)
        requests = {
            role: WORKER.build_request(
                campaign_id=sources.running_requests[role][
                    "campaign_id"
                ],
                operation_id=sources.running_requests[role][
                    "operation_id"
                ],
                release_sha=sources.running_requests[role]["release_sha"],
                release_tree_sha=sources.running_requests[role][
                    "release_tree_sha"
                ],
                role=role,
                worker_sha256="f" * 64,
                inventory_agent_sha256=sources.running_requests[role][
                    "agent_sha256"
                ],
                contract_worker_sha256=sources.running_requests[role][
                    "contract_worker_sha256"
                ],
                role_manifest_path=sources.running_requests[role][
                    "role_manifest_path"
                ],
                role_manifest_sha256=sources.running_requests[role][
                    "role_manifest_sha256"
                ],
                pre_inventory_request=sources.running_requests[role],
                pre_inventory_response=sources.running_responses[role],
                controller_challenge_sha256=hashlib.sha256(
                    f"retry-{role}".encode("ascii")
                ).hexdigest(),
                issued_at=issued_at,
                expires_at=issued_at + timedelta(minutes=20),
            )
            for role in MODULE.ROLES
        }
        backends = {
            role: WorkerBackend(
                role=role,
                container_id=sources.running_responses[role][
                    "prepared_container_id"
                ],
                network_id=sources.running_responses[role][
                    "prepared_network_id"
                ],
            )
            for role in MODULE.ROLES
        }
        lose_once = {"webapp_fi": True}

        def invoke(
            role,
            request,
            *,
            authority_check,
            cancellation,
        ):
            def authority(checkpoint):
                authority_check(role, checkpoint)
                return not cancellation.is_set()

            ticks = iter(
                (
                    issued_at + timedelta(seconds=1),
                    issued_at + timedelta(seconds=2),
                )
            )
            result = WORKER.execute(
                request,
                apply=True,
                confirm=WORKER.confirmation_phrase(request),
                authority=authority,
                backend=backends[role],
                now=issued_at,
                clock=lambda: next(ticks),
            )
            if lose_once.get(role):
                lose_once[role] = False
                raise MODULE.StartupNormalizationPhaseError(
                    "simulated lost host result"
                )
            return result

        with self.assertRaisesRegex(
            MODULE.StartupNormalizationPhaseError,
            "workers failed closed",
        ):
            MODULE._run_workers_concurrently(
                requests,
                worker_invoker=invoke,
                authority_check=lambda *_args: None,
            )
        self.assertTrue(
            all(not backend.database_running for backend in backends.values())
        )
        retried = MODULE._run_workers_concurrently(
            requests,
            worker_invoker=invoke,
            authority_check=lambda *_args: None,
        )
        self.assertEqual(set(retried), set(MODULE.ROLES))
        self.assertTrue(
            all(
                result["database_start_performed"]
                for result in retried.values()
            )
        )
        self.assertTrue(
            all(not backend.database_running for backend in backends.values())
        )

    def test_worker_cleanup_reaps_unobserved_setsid_adopted_child(self):
        invoker = self._bare_worker_invoker()
        process = None
        descriptor = None
        root_identity = None
        child_identity = None
        tracked = set()
        try:
            MODULE.PROCESS._enable_child_subreaper()  # noqa: SLF001
            with invoker._ownership_lock:
                invoker._direct_child_baseline = (
                    MODULE.PROCESS._direct_child_baseline()  # noqa: SLF001
                )
                invoker._active_session_count = 1
                invoker._active_sessions[1] = None
                process = subprocess.Popen(
                    [
                        "/usr/bin/python3",
                        "-c",
                        (
                            "import os,time\n"
                            "child=os.fork()\n"
                            "if child == 0:\n"
                            " os.setsid()\n"
                            " os.write(1,(str(os.getpid())+'\\n').encode())\n"
                            " time.sleep(60)\n"
                            " os._exit(0)\n"
                            "time.sleep(0.1)\n"
                            "os._exit(0)\n"
                        ),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    start_new_session=True,
                )
                invoker._active_root_pids[1] = process.pid
                descriptor = os.pidfd_open(process.pid, 0)
                root_identity = MODULE.PROCESS._process_identity(  # noqa: SLF001
                    process.pid
                )
                self.assertIsNotNone(root_identity)
                tracked.add(root_identity)
                invoker._active_sessions[1] = root_identity
                invoker._active_roots[root_identity.key] = 1
                invoker._identity_owners[root_identity.key] = 1
            self.assertIsNotNone(process.stdout)
            child_pid = int(process.stdout.readline().decode("ascii"))
            child_identity = MODULE.PROCESS._process_identity(  # noqa: SLF001
                child_pid
            )
            self.assertIsNotNone(child_identity)
            process.wait(timeout=2)
            # No ownership refresh occurred before the root exited.  The
            # detached child is discoverable only as a new subreaper-adopted
            # direct child relative to the process-wide baseline.
            invoker._terminate(
                process,
                root_descriptor=descriptor,
                root_identity=root_identity,
                tracked=tracked,
                session_token=1,
            )
            self.assertFalse(
                MODULE.PROCESS._identity_is_live(  # noqa: SLF001
                    child_identity
                )
            )
        finally:
            if (
                child_identity is not None
                and MODULE.PROCESS._identity_is_live(  # noqa: SLF001
                    child_identity
                )
            ):
                MODULE.PROCESS._signal_identity(  # noqa: SLF001
                    child_identity,
                    9,
                )
            if process is not None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                for stream in (
                    process.stdin,
                    process.stdout,
                    process.stderr,
                ):
                    if stream is not None:
                        stream.close()
            if descriptor is not None:
                os.close(descriptor)
            if invoker._active_session_count:
                invoker._end_session(1, root_identity)

    def test_worker_ownership_refuses_reused_root_and_group(self):
        invoker = self._bare_worker_invoker()
        root = MODULE.PROCESS.ProcessIdentity(
            pid=41001,
            parent_pid=os.getpid(),
            process_group=41001,
            session_id=41001,
            start_time=100,
            state="R",
        )
        reused = MODULE.PROCESS.ProcessIdentity(
            pid=41001,
            parent_pid=999,
            process_group=41001,
            session_id=41001,
            start_time=200,
            state="R",
        )
        reused_group_member = MODULE.PROCESS.ProcessIdentity(
            pid=41002,
            parent_pid=999,
            process_group=41001,
            session_id=41001,
            start_time=300,
            state="R",
        )
        invoker._active_session_count = 1
        invoker._active_sessions[1] = root
        invoker._active_root_pids[1] = root.pid
        invoker._active_roots[root.key] = 1
        invoker._identity_owners[root.key] = 1
        invoker._direct_child_baseline = frozenset()
        tracked = {root}
        with (
            mock.patch.object(
                MODULE.PROCESS,
                "_process_snapshot",
                return_value={
                    reused.pid: reused,
                    reused_group_member.pid: reused_group_member,
                },
            ),
            mock.patch.object(
                MODULE.PROCESS,
                "_identity_is_live",
                side_effect=lambda identity: (
                    identity.start_time in {200, 300}
                ),
            ),
        ):
            live = invoker._refresh_owned(
                root,
                tracked,
                session_token=1,
            )
        self.assertEqual(live, set())
        self.assertEqual(tracked, {root})
        with mock.patch.object(
            MODULE.PROCESS,
            "_process_snapshot",
            return_value={},
        ):
            invoker._end_session(1, root)

    def test_sibling_cannot_claim_preassociated_adopted_child(self):
        invoker = self._bare_worker_invoker()
        root_a = MODULE.PROCESS.ProcessIdentity(
            pid=42001,
            parent_pid=os.getpid(),
            process_group=42001,
            session_id=42001,
            start_time=101,
            state="R",
        )
        root_b = MODULE.PROCESS.ProcessIdentity(
            pid=42002,
            parent_pid=os.getpid(),
            process_group=42002,
            session_id=42002,
            start_time=102,
            state="R",
        )
        child = MODULE.PROCESS.ProcessIdentity(
            pid=42003,
            parent_pid=root_a.pid,
            process_group=42003,
            session_id=42003,
            start_time=103,
            state="R",
        )
        invoker._active_session_count = 2
        invoker._active_sessions = {1: root_a, 2: root_b}
        invoker._active_root_pids = {1: root_a.pid, 2: root_b.pid}
        invoker._active_roots = {root_a.key: 1, root_b.key: 2}
        invoker._identity_owners = {root_a.key: 1, root_b.key: 2}
        tracked_a = {root_a}
        tracked_b = {root_b}
        with (
            mock.patch.object(
                MODULE.PROCESS,
                "_process_snapshot",
                return_value={
                    root_a.pid: root_a,
                    root_b.pid: root_b,
                    child.pid: child,
                },
            ),
            mock.patch.object(
                MODULE.PROCESS,
                "_identity_is_live",
                return_value=True,
            ),
        ):
            invoker._refresh_owned(
                root_a,
                tracked_a,
                session_token=1,
            )
        self.assertEqual(invoker._identity_owners[child.key], 1)
        adopted = replace(child, parent_pid=os.getpid())
        with (
            mock.patch.object(
                MODULE.PROCESS,
                "_process_snapshot",
                return_value={
                    root_b.pid: root_b,
                    adopted.pid: adopted,
                },
            ),
            mock.patch.object(
                MODULE.PROCESS,
                "_identity_is_live",
                return_value=True,
            ),
        ):
            live_b = invoker._refresh_owned(
                root_b,
                tracked_b,
                session_token=2,
            )
        self.assertNotIn(adopted, live_b)
        self.assertNotIn(adopted, tracked_b)
        self.assertEqual(invoker._identity_owners[child.key], 1)
        self.assertFalse(invoker._ownership_abort.is_set())

    def test_unattributable_adopted_child_aborts_without_sibling_claim(self):
        invoker = self._bare_worker_invoker()
        root_a = MODULE.PROCESS.ProcessIdentity(
            pid=43001,
            parent_pid=os.getpid(),
            process_group=43001,
            session_id=43001,
            start_time=201,
            state="R",
        )
        root_b = MODULE.PROCESS.ProcessIdentity(
            pid=43002,
            parent_pid=os.getpid(),
            process_group=43002,
            session_id=43002,
            start_time=202,
            state="R",
        )
        orphan = MODULE.PROCESS.ProcessIdentity(
            pid=43003,
            parent_pid=os.getpid(),
            process_group=43003,
            session_id=43003,
            start_time=203,
            state="R",
        )
        invoker._active_session_count = 2
        invoker._active_sessions = {1: root_a, 2: root_b}
        invoker._active_root_pids = {1: root_a.pid, 2: root_b.pid}
        invoker._active_roots = {root_a.key: 1, root_b.key: 2}
        invoker._identity_owners = {root_a.key: 1, root_b.key: 2}
        tracked_b = {root_b}
        with (
            mock.patch.object(
                MODULE.PROCESS,
                "_process_snapshot",
                return_value={
                    root_b.pid: root_b,
                    orphan.pid: orphan,
                },
            ),
            mock.patch.object(
                MODULE.PROCESS,
                "_identity_is_live",
                return_value=True,
            ),
        ):
            live_b = invoker._refresh_owned(
                root_b,
                tracked_b,
                session_token=2,
            )
        self.assertNotIn(orphan, live_b)
        self.assertNotIn(orphan.key, invoker._identity_owners)
        self.assertIn(orphan, invoker._quarantined_identities)
        self.assertTrue(invoker._ownership_abort.is_set())

    def test_end_session_reconciles_child_from_final_teardown_window(self):
        invoker = self._bare_worker_invoker()
        root = MODULE.PROCESS.ProcessIdentity(
            pid=44001,
            parent_pid=os.getpid(),
            process_group=44001,
            session_id=44001,
            start_time=301,
            state="Z",
        )
        invoker._direct_child_baseline = (
            MODULE.PROCESS._direct_child_baseline()  # noqa: SLF001
        )
        invoker._active_session_count = 1
        invoker._active_sessions = {1: root}
        invoker._active_root_pids = {1: root.pid}
        invoker._active_roots = {root.key: 1}
        invoker._identity_owners = {root.key: 1}
        process = subprocess.Popen(
            [
                "/usr/bin/python3",
                "-c",
                "import time; time.sleep(60)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        child_identity = MODULE.PROCESS._process_identity(  # noqa: SLF001
            process.pid
        )
        self.assertIsNotNone(child_identity)
        try:
            with (
                mock.patch.object(
                    MODULE,
                    "WORKER_POLL_SECONDS",
                    0.01,
                ),
                mock.patch.object(
                    MODULE,
                    "WORKER_TERM_GRACE_SECONDS",
                    0.10,
                ),
                mock.patch.object(
                    MODULE,
                    "WORKER_KILL_GRACE_SECONDS",
                    0.50,
                ),
            ):
                invoker._end_session(
                    1,
                    root,
                    tracked={root},
                )
            self.assertFalse(
                MODULE.PROCESS._identity_is_live(  # noqa: SLF001
                    child_identity
                )
            )
            self.assertEqual(invoker._active_session_count, 0)
            self.assertEqual(invoker._active_sessions, {})
            self.assertEqual(invoker._active_root_pids, {})
            self.assertEqual(invoker._active_roots, {})
            self.assertEqual(invoker._identity_owners, {})
            self.assertEqual(invoker._quarantined_identities, set())
            self.assertFalse(invoker._ownership_abort.is_set())
        finally:
            if MODULE.PROCESS._identity_is_live(  # noqa: SLF001
                child_identity
            ):
                MODULE.PROCESS._signal_identity(  # noqa: SLF001
                    child_identity,
                    signal.SIGKILL,
                )
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def test_missing_root_identity_reconciles_bootstrap_orphan(self):
        invoker = self._bare_worker_invoker()
        request = {
            "role": "bot_fi",
            "worker_path": "/exact/worker.py",
            "request_binding_sha256": "d" * 64,
        }
        original_identity = MODULE.PROCESS._process_identity  # noqa: SLF001
        roots = []
        denied = set()
        child_pid = None
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child.pid"

            def factory(*args, **kwargs):
                process = subprocess.Popen(*args, **kwargs)
                roots.append(process)
                deadline = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                return process

            def identity(pid):
                if roots and pid == roots[0].pid and pid not in denied:
                    denied.add(pid)
                    return None
                return original_identity(pid)

            invoker.session_factory = factory
            with (
                mock.patch.object(
                    WORKER,
                    "validate_request",
                    return_value=request,
                ),
                mock.patch.object(
                    invoker,
                    "_argv",
                    return_value=(
                        "/usr/bin/python3",
                        "-c",
                        (
                            "import os,time\n"
                            "child=os.fork()\n"
                            "if child == 0:\n"
                            " os.setsid()\n"
                            f" open({str(marker)!r},'w').write(str(os.getpid()))\n"
                            " time.sleep(60)\n"
                            " os._exit(0)\n"
                            "os._exit(0)\n"
                        ),
                    ),
                ),
                mock.patch.object(
                    MODULE.PROCESS,
                    "_process_identity",
                    side_effect=identity,
                ),
                mock.patch.object(
                    MODULE,
                    "WORKER_TERM_GRACE_SECONDS",
                    0.10,
                ),
                mock.patch.object(
                    MODULE,
                    "WORKER_KILL_GRACE_SECONDS",
                    0.50,
                ),
                self.assertRaisesRegex(
                    MODULE.StartupNormalizationPhaseError,
                    "identity differs",
                ),
            ):
                invoker(
                    "bot_fi",
                    request,
                    authority_check=lambda *_args: None,
                    cancellation=threading.Event(),
                )
            child_pid = int(marker.read_text(encoding="ascii"))
        child_identity = original_identity(child_pid)
        self.assertTrue(
            child_identity is None
            or not MODULE.PROCESS._identity_is_live(  # noqa: SLF001
                child_identity
            )
        )
        self.assertEqual(invoker._active_session_count, 0)

    def test_concurrent_orphan_ambiguity_cancels_without_residue(self):
        invoker = self._bare_worker_invoker()
        processes: dict[str, subprocess.Popen[bytes]] = {}
        process_lock = threading.Lock()
        cancellations: dict[str, threading.Event] = {}
        child_identity = None
        coordinator_errors: list[str] = []
        original_refresh = invoker._refresh_owned
        original_identity = MODULE.PROCESS._process_identity  # noqa: SLF001
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            go_marker = root / "go"
            child_marker = root / "detached-child.pid"
            program = (
                "import os,sys,time\n"
                "go,child_marker,role=sys.argv[1:]\n"
                "while not os.path.exists(go): time.sleep(0.005)\n"
                "if role == 'bot_fi':\n"
                " child=os.fork()\n"
                " if child == 0:\n"
                "  os.setsid()\n"
                "  with open(child_marker,'w',encoding='ascii') as stream:\n"
                "   stream.write(str(os.getpid()))\n"
                "   stream.flush()\n"
                "  time.sleep(60)\n"
                "  os._exit(0)\n"
                " while not os.path.exists(child_marker): time.sleep(0.001)\n"
                " os._exit(0)\n"
                "time.sleep(60)\n"
            )

            def argv(role, _request):
                return (
                    "/usr/bin/python3",
                    "-c",
                    program,
                    os.fspath(go_marker),
                    os.fspath(child_marker),
                    role,
                )

            def factory(*args, **kwargs):
                process = subprocess.Popen(*args, **kwargs)
                role = str(args[0][-1])
                with process_lock:
                    processes[role] = process
                return process

            def refresh(root_identity, tracked, *, session_token):
                with process_lock:
                    bot_process = processes.get("bot_fi")
                if (
                    bot_process is not None
                    and root_identity.pid == bot_process.pid
                    and child_marker.exists()
                ):
                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline:
                        observed = original_identity(root_identity.pid)
                        if (
                            observed is None
                            or observed.start_time
                            != root_identity.start_time
                            or observed.state == "Z"
                        ):
                            break
                        time.sleep(0.005)
                    while (
                        not invoker._ownership_abort.is_set()
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.005)
                return original_refresh(
                    root_identity,
                    tracked,
                    session_token=session_token,
                )

            def release_workers() -> None:
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    with invoker._ownership_lock:
                        active = invoker._active_session_count
                    if active == len(MODULE.ROLES):
                        go_marker.touch()
                        return
                    time.sleep(0.005)
                coordinator_errors.append(
                    "all worker sessions did not become active"
                )
                go_marker.touch()

            def invoke(role, request, *, authority_check, cancellation):
                cancellations[role] = cancellation
                return invoker(
                    role,
                    request,
                    authority_check=authority_check,
                    cancellation=cancellation,
                )

            requests = {
                role: {
                    "role": role,
                    "worker_path": "/exact/worker.py",
                    "request_binding_sha256": str(index + 1) * 64,
                }
                for index, role in enumerate(MODULE.ROLES)
            }
            invoker.session_factory = factory
            coordinator = threading.Thread(
                target=release_workers,
                daemon=True,
            )
            coordinator.start()
            try:
                with (
                    mock.patch.object(
                        WORKER,
                        "validate_request",
                        side_effect=lambda value: dict(value),
                    ),
                    mock.patch.object(
                        invoker,
                        "_argv",
                        side_effect=argv,
                    ),
                    mock.patch.object(
                        invoker,
                        "_refresh_owned",
                        side_effect=refresh,
                    ),
                    mock.patch.object(
                        MODULE,
                        "WORKER_SESSION_TIMEOUT_SECONDS",
                        3.0,
                    ),
                    mock.patch.object(
                        MODULE,
                        "WORKER_POLL_SECONDS",
                        0.01,
                    ),
                    mock.patch.object(
                        MODULE,
                        "WORKER_TERM_GRACE_SECONDS",
                        0.10,
                    ),
                    mock.patch.object(
                        MODULE,
                        "WORKER_KILL_GRACE_SECONDS",
                        0.50,
                    ),
                    self.assertRaisesRegex(
                        MODULE.StartupNormalizationPhaseError,
                        "parallel normalization workers failed closed",
                    ),
                ):
                    MODULE._run_workers_concurrently(
                        requests,
                        worker_invoker=invoke,
                        authority_check=lambda *_args: None,
                    )
                coordinator.join(timeout=3)
                self.assertFalse(coordinator.is_alive())
                self.assertEqual(coordinator_errors, [])
                self.assertTrue(child_marker.exists())
                child_pid = int(
                    child_marker.read_text(encoding="ascii")
                )
                child_identity = original_identity(child_pid)
                self.assertEqual(set(processes), set(MODULE.ROLES))
                self.assertEqual(set(cancellations), set(MODULE.ROLES))
                self.assertEqual(
                    len({id(value) for value in cancellations.values()}),
                    1,
                )
                self.assertTrue(next(iter(cancellations.values())).is_set())
                self.assertTrue(
                    all(
                        process.returncode is not None
                        for process in processes.values()
                    )
                )
                self.assertTrue(
                    child_identity is None
                    or not MODULE.PROCESS._identity_is_live(  # noqa: SLF001
                        child_identity
                    )
                )
                self.assertEqual(invoker._active_session_count, 0)
                self.assertEqual(invoker._active_sessions, {})
                self.assertEqual(invoker._active_root_pids, {})
                self.assertEqual(invoker._active_roots, {})
                self.assertEqual(invoker._identity_owners, {})
                self.assertEqual(invoker._quarantined_identities, set())
                self.assertFalse(invoker._ownership_abort.is_set())
            finally:
                go_marker.touch(exist_ok=True)
                coordinator.join(timeout=3)
                if (
                    child_identity is None
                    and child_marker.exists()
                ):
                    child_identity = original_identity(
                        int(child_marker.read_text(encoding="ascii"))
                    )
                if (
                    child_identity is not None
                    and MODULE.PROCESS._identity_is_live(  # noqa: SLF001
                        child_identity
                    )
                ):
                    MODULE.PROCESS._signal_identity(  # noqa: SLF001
                        child_identity,
                        signal.SIGKILL,
                    )
                for process in processes.values():
                    if process.poll() is None:
                        process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                    for stream in (
                        process.stdin,
                        process.stdout,
                        process.stderr,
                    ):
                        if stream is not None and not stream.closed:
                            stream.close()

    def test_worker_session_stalled_stdin_is_bounded_and_reaped(self):
        invoker = self._bare_worker_invoker()
        request = {
            "role": "bot_fi",
            "worker_path": "/exact/worker.py",
            "request_binding_sha256": "a" * 64,
            "padding": "x" * (1024 * 1024),
        }
        processes = []

        def factory(*args, **kwargs):
            process = subprocess.Popen(*args, **kwargs)
            processes.append(process)
            return process

        invoker.session_factory = factory
        started = time.monotonic()
        with (
            mock.patch.object(
                WORKER,
                "validate_request",
                return_value=request,
            ),
            mock.patch.object(
                invoker,
                "_argv",
                return_value=(
                    "/usr/bin/python3",
                    "-c",
                    "import time; time.sleep(60)",
                ),
            ),
            mock.patch.object(
                MODULE,
                "WORKER_SESSION_TIMEOUT_SECONDS",
                0.20,
            ),
            mock.patch.object(
                MODULE,
                "WORKER_TERM_GRACE_SECONDS",
                0.10,
            ),
            mock.patch.object(
                MODULE,
                "WORKER_KILL_GRACE_SECONDS",
                0.50,
            ),
            self.assertRaisesRegex(
                MODULE.StartupNormalizationPhaseError,
                "timed out",
            ),
        ):
            invoker(
                "bot_fi",
                request,
                authority_check=lambda *_args: None,
                cancellation=threading.Event(),
            )
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)
        self.assertEqual(invoker._active_session_count, 0)

    def test_worker_session_cancellation_does_not_interrupt_cleanup(self):
        invoker = self._bare_worker_invoker()
        request = {
            "role": "bot_fi",
            "worker_path": "/exact/worker.py",
            "request_binding_sha256": "b" * 64,
        }
        processes = []
        cancellation = threading.Event()

        def factory(*args, **kwargs):
            process = subprocess.Popen(*args, **kwargs)
            processes.append(process)
            return process

        invoker.session_factory = factory

        def cancel() -> None:
            time.sleep(0.15)
            cancellation.set()

        canceller = threading.Thread(target=cancel, daemon=True)
        canceller.start()
        with (
            mock.patch.object(
                WORKER,
                "validate_request",
                return_value=request,
            ),
            mock.patch.object(
                invoker,
                "_argv",
                return_value=(
                    "/usr/bin/python3",
                    "-c",
                    (
                        "import signal,time\n"
                        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                        "time.sleep(60)\n"
                    ),
                ),
            ),
            mock.patch.object(
                MODULE,
                "WORKER_SESSION_TIMEOUT_SECONDS",
                2.0,
            ),
            mock.patch.object(
                MODULE,
                "WORKER_TERM_GRACE_SECONDS",
                0.15,
            ),
            mock.patch.object(
                MODULE,
                "WORKER_KILL_GRACE_SECONDS",
                0.50,
            ),
            self.assertRaisesRegex(
                MODULE.StartupNormalizationPhaseError,
                "cancelled",
            ),
        ):
            invoker(
                "bot_fi",
                request,
                authority_check=lambda *_args: None,
                cancellation=cancellation,
            )
        canceller.join(timeout=1)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)
        self.assertEqual(invoker._active_session_count, 0)

    def test_worker_session_primary_error_precedes_cleanup_error(self):
        invoker = self._bare_worker_invoker()
        request = {
            "role": "bot_fi",
            "worker_path": "/exact/worker.py",
            "request_binding_sha256": "c" * 64,
        }
        original_terminate = invoker._terminate

        def terminate_then_fail(*args, **kwargs):
            original_terminate(*args, **kwargs)
            raise RuntimeError("cleanup failed after reconciliation")

        with (
            mock.patch.object(
                WORKER,
                "validate_request",
                return_value=request,
            ),
            mock.patch.object(
                invoker,
                "_argv",
                return_value=(
                    "/usr/bin/python3",
                    "-c",
                    (
                        "import os,time\n"
                        "os.write(2,b'protocol stderr')\n"
                        "time.sleep(60)\n"
                    ),
                ),
            ),
            mock.patch.object(
                invoker,
                "_terminate",
                side_effect=terminate_then_fail,
            ),
            self.assertRaisesRegex(
                MODULE.StartupNormalizationPhaseError,
                "emitted stderr",
            ) as caught,
        ):
            invoker(
                "bot_fi",
                request,
                authority_check=lambda *_args: None,
                cancellation=threading.Event(),
            )
        self.assertTrue(
            any(
                "cleanup also failed" in note
                for note in getattr(caught.exception, "__notes__", ())
            )
        )
        self.assertEqual(invoker._active_session_count, 0)


if __name__ == "__main__":
    unittest.main()
