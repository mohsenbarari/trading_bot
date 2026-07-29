from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import orchestrate_production_shadow_prepared_clone_inventory as PREPARED
from scripts import orchestrate_production_shadow_pristine_redis as MODULE
from scripts import production_shadow_cutover_controller as CONTROLLER
from scripts import verify_production_shadow_phase_evidence as VERIFY
from tests import test_orchestrate_production_shadow_prepared_clone_inventory as PREPARED_FIXTURE
from tests import test_orchestrate_production_shadow_freeze_snapshot_phases as FROZEN_FIXTURE
from tests import test_verify_production_shadow_phase_evidence as VERIFY_FIXTURE
from tests.test_production_shadow_cutover_controller import manifest_payload


BASE = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def coherent_prior_evidence() -> tuple[dict[str, dict], dict[str, str]]:
    records: dict[str, dict] = {}
    digests: dict[str, str] = {}
    for phase in CONTROLLER.PHASES[
        : CONTROLLER.PHASES.index(MODULE.PHASE)
    ]:
        document = VERIFY_FIXTURE.evidence_for(
            phase,
            captured_at=BASE,
        )
        prior_rows = [
            {"phase": prior, "evidence_sha256": digests[prior]}
            for prior in records
        ]
        wrapped = {
            prior: {
                "document": records[prior],
                "file_sha256": digests[prior],
            }
            for prior in records
        }
        prior_claims = VERIFY._derive_prior_claim_rows(
            phase=phase,
            prior_digests=dict(digests),
            prior_records=wrapped,
            campaign_id=VERIFY_FIXTURE.CAMPAIGN_ID,
            operation_id=VERIFY_FIXTURE.OPERATION_ID,
            release_sha=VERIFY_FIXTURE.RELEASE_SHA,
            legacy_release_sha=VERIFY_FIXTURE.LEGACY_RELEASE_SHA,
            manifest_sha256=VERIFY_FIXTURE.MANIFEST_SHA256,
            plan_sha256=VERIFY_FIXTURE.PLAN_SHA256,
        )
        dynamic = {
            name: row["value"]
            for name, row in document["claims"].items()
            if VERIFY.PHASE_CLAIM_RULES[phase][name].kind != "exact"
        }
        role_requests = {
            row["role"]: row["request_sha256"]
            for row in document["role_attestations"]
        }
        role_sources = {
            row["role"]: row["source_artifact_sha256"]
            for row in document["role_attestations"]
        }
        role_observed = {
            row["role"]: row["observed_at"]
            for row in document["role_attestations"]
        }
        claim_sources = {
            name: row["source_sha256"]
            for name, row in document["claims"].items()
        }
        phase_input = {
            "manifest_sha256": VERIFY_FIXTURE.MANIFEST_SHA256,
            "manifest_artifacts_sha256": hashlib.sha256(
                canonical_json(VERIFY_FIXTURE.MANIFEST_ARTIFACTS)
            ).hexdigest(),
            "prior_phase_evidence": prior_rows,
            "prior_claim_bindings": prior_claims,
            "dynamic_claim_values": dynamic,
            "claim_source_sha256": {
                name: claim_sources[name]
                for name in sorted(claim_sources)
            },
            "role_request_sha256": role_requests,
            "role_source_artifact_sha256": role_sources,
            "role_observed_at": role_observed,
        }
        document["prior_phase_evidence"] = prior_rows
        document["prior_phase_evidence_closure_sha256"] = (
            hashlib.sha256(canonical_json(prior_rows)).hexdigest()
        )
        document["prior_claim_bindings"] = prior_claims
        document["phase_input_closure_sha256"] = hashlib.sha256(
            canonical_json(phase_input)
        ).hexdigest()
        digest = hashlib.sha256(
            canonical_json(document) + b"\n"
        ).hexdigest()
        VERIFY.verify_phase_evidence(
            document,
            evidence_file_sha256=digest,
            expected_phase=phase,
            expected_campaign_id=VERIFY_FIXTURE.CAMPAIGN_ID,
            expected_operation_id=VERIFY_FIXTURE.OPERATION_ID,
            expected_release_sha=VERIFY_FIXTURE.RELEASE_SHA,
            expected_legacy_release_sha=(
                VERIFY_FIXTURE.LEGACY_RELEASE_SHA
            ),
            expected_manifest_sha256=VERIFY_FIXTURE.MANIFEST_SHA256,
            expected_plan_sha256=VERIFY_FIXTURE.PLAN_SHA256,
            expected_approval_sha256=VERIFY_FIXTURE.APPROVAL_SHA256,
            expected_phase_evidence_schema_sha256=(
                VERIFY_FIXTURE.PHASE_EVIDENCE_CONTRACT_SHA256
            ),
            expected_manifest_artifacts=dict(
                VERIFY_FIXTURE.MANIFEST_ARTIFACTS
            ),
            expected_role_request_sha256=role_requests,
            expected_role_source_artifact_sha256=role_sources,
            expected_role_observed_at=role_observed,
            expected_dynamic_claim_values=dynamic,
            expected_claim_source_sha256=claim_sources,
            expected_prior_phase_evidence_sha256=dict(digests),
            prior_phase_evidence_records=wrapped,
            now=BASE,
        )
        records[phase] = document
        digests[phase] = digest
    return records, digests


class Live:
    def check(self) -> None:
        return None


@contextmanager
def live_context(_descriptor: int):
    yield Live()


class LostLive:
    def check(self) -> None:
        raise PREPARED.PreparedCloneInventoryError("lost")


@contextmanager
def lost_live_context(_descriptor: int):
    yield LostLive()


class Fixture:
    def __init__(
        self,
        root: Path,
        *,
        start_phase: bool = True,
        publish_receipt: bool = True,
    ):
        self.root = root
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.output_root = root / "evidence"
        self.output_root.mkdir(mode=0o700)
        self.manifest = manifest_payload()
        self.manifest["campaign_id"] = VERIFY_FIXTURE.CAMPAIGN_ID
        self.manifest["operation_id"] = VERIFY_FIXTURE.OPERATION_ID
        self.manifest["release_sha"] = VERIFY_FIXTURE.RELEASE_SHA
        self.manifest["legacy_release_sha"] = (
            VERIFY_FIXTURE.LEGACY_RELEASE_SHA
        )
        self.manifest["artifacts"] = copy.deepcopy(
            VERIFY_FIXTURE.MANIFEST_ARTIFACTS
        )
        CONTROLLER.validate_manifest(self.manifest)
        self.prior_records, self.prior_digests = coherent_prior_evidence()
        journal_root = root / "journal"
        journal_root.mkdir(mode=0o700)
        self.journal = CONTROLLER.ProductionCutoverJournal(
            journal_root / "journal.json",
            owner_uid=os.geteuid(),
        )
        with mock.patch.object(
            CONTROLLER,
            "_now",
            return_value=BASE.isoformat(),
        ):
            self.journal.create(
                manifest_sha256=VERIFY_FIXTURE.MANIFEST_SHA256,
                plan_sha256=VERIFY_FIXTURE.PLAN_SHA256,
                campaign_id=self.manifest["campaign_id"],
                operation_id=self.manifest["operation_id"],
                release_sha=self.manifest["release_sha"],
                legacy_release_sha=self.manifest[
                    "legacy_release_sha"
                ],
            )
            for index, phase in enumerate(
                CONTROLLER.PHASES[
                    : CONTROLLER.PHASES.index(MODULE.PHASE)
                ]
            ):
                self.journal.begin_phase(phase)
                self.journal.complete_phase(
                    phase,
                    verification=CONTROLLER.VerifiedPhaseCompletion(
                        phase=phase,
                        evidence_sha256=self.prior_digests[phase],
                        receipt_sha256=(
                            hashlib.sha256(
                                f"receipt-{index}".encode("ascii")
                            ).hexdigest()
                        ),
                    ),
                )
            state = (
                self.journal.begin_phase(MODULE.PHASE)
                if start_phase
                else self.journal.load()
            )
        self.context = MODULE.EvidenceContext(
            manifest_path=root / "manifest.json",
            approval_path=root / "approval.json",
            approval_policy_path=root / "policy.json",
            journal_path=Path(
                self.manifest["deployment"]["controller_journal_path"]
            ),
            manifest=self.manifest,
            manifest_sha256=VERIFY_FIXTURE.MANIFEST_SHA256,
            plan={"plan_sha256": VERIFY_FIXTURE.PLAN_SHA256},
            plan_sha256=VERIFY_FIXTURE.PLAN_SHA256,
            journal=state,
            prior_records=self.prior_records,
            prior_digests=self.prior_digests,
            prior_paths={
                phase: root / "prior" / f"{phase}.json"
                for phase in self.prior_records
            },
            output_root=self.output_root,
        )
        self.spec = (
            self._publish_stopped_receipt()
            if publish_receipt
            else None
        )

    def _publish_stopped_receipt(
        self,
        *,
        challenge: str = "9" * 64,
        issued_at: datetime | None = None,
    ) -> MODULE.PersistedReceiptSpec:
        issued_at = (
            BASE + timedelta(seconds=2)
            if issued_at is None
            else issued_at
        )
        prior_issued_at = issued_at - timedelta(seconds=22)
        running_inputs = PREPARED.CollectionInputs(
            campaign_id=self.manifest["campaign_id"],
            operation_id=self.manifest["operation_id"],
            release_sha=self.manifest["release_sha"],
            release_tree_sha=self.manifest["release_tree_sha"],
            agent_sha256="1" * 64,
            roles={
                "bot_fi": PREPARED.RoleBinding(
                    contract_worker_sha256="2" * 64,
                    role_manifest_sha256="5" * 64,
                ),
                "webapp_fi": PREPARED.RoleBinding(
                    contract_worker_sha256="2" * 64,
                    role_manifest_sha256="6" * 64,
                ),
                "webapp_ir": PREPARED.RoleBinding(
                    contract_worker_sha256="3" * 64,
                    role_manifest_sha256="7" * 64,
                ),
            },
        )
        running_requests = PREPARED._request_set(
            running_inputs,
            challenge="8" * 64,
            issued_at=prior_issued_at,
            expires_at=(
                prior_issued_at
                + timedelta(
                    seconds=PREPARED.REQUEST_LIFETIME_SECONDS
                )
            ),
        )
        running_responses: dict[str, dict] = {}
        for index, role in enumerate(PREPARED.ROLES):
            running_responses[role] = PREPARED_FIXTURE._response(
                running_requests[role],
                captured_at=(
                    prior_issued_at
                    + timedelta(seconds=2 + index * 2)
                ),
                marker=chr(ord("a") + index),
            )
        stopped_inputs = replace(
            running_inputs,
            expected_database_state="stopped",
            prior_requests=running_requests,
            prior_responses=running_responses,
        )
        requests = PREPARED._request_set(
            stopped_inputs,
            challenge=challenge,
            issued_at=issued_at,
            expires_at=issued_at
            + timedelta(seconds=PREPARED.REQUEST_LIFETIME_SECONDS),
        )
        responses: dict[str, dict] = {}
        command_times: dict[
            str,
            tuple[datetime, datetime],
        ] = {}
        for index, role in enumerate(PREPARED.ROLES):
            started = issued_at + timedelta(seconds=index * 2 + 1)
            captured = started + timedelta(microseconds=500_000)
            completed = started + timedelta(seconds=1)
            responses[role] = PREPARED_FIXTURE._response(
                requests[role],
                captured_at=captured,
                marker=chr(ord("d") + index),
            )
            command_times[role] = (started, completed)
        aggregate = PREPARED.build_aggregate(
            inputs=stopped_inputs,
            requests=requests,
            responses=responses,
            command_times=command_times,
            now=issued_at + timedelta(seconds=8),
        )
        publication = PREPARED.publish_receipt_create_only(
            aggregate,
            requests=requests,
            responses=responses,
            output_root=self.output_root,
            now=issued_at + timedelta(seconds=8),
        )
        return MODULE.PersistedReceiptSpec(
            receipt_path=Path(publication["path"]),
            controller_challenge_sha256=challenge,
            aggregate_artifact_sha256=publication["sha256"],
            final_snapshot_request_path=self.root
            / "frozen-request.json",
            final_snapshot_request_sha256="a" * 64,
            final_snapshot_aggregate_path=self.root
            / "final-snapshot-aggregate.json",
            final_snapshot_aggregate_sha256="b" * 64,
        )

    def patch_root(self):
        return mock.patch.object(
            MODULE,
            "_manifest_output_root",
            return_value=self.output_root,
        )

    def closure(self, *, now: datetime | None = None) -> dict:
        with (
            self.patch_root(),
            mock.patch.object(
                MODULE,
                "_validate_final_snapshot_source_closure",
                return_value={
                    "source_closure_sha256": "c" * 64,
                    "aggregate_artifact_sha256": "d" * 64,
                    "legacy_redis_exclusion_sha256": "e" * 64,
                },
            ),
        ):
            return MODULE.load_pristine_closure(
                self.context,
                self.spec,
                now=now or BASE + timedelta(seconds=10),
            )


class PristineRedisPhaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = Fixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_is_local_and_never_accepts_claim_values(self):
        plan = MODULE.build_plan(
            operation_id=VERIFY_FIXTURE.OPERATION_ID,
            release_sha=VERIFY_FIXTURE.RELEASE_SHA,
            source_available=False,
        )
        self.assertFalse(plan["apply_supported"])
        self.assertFalse(plan["production_contacted"])
        self.assertFalse(plan["redis_mutation_allowed"])
        self.assertFalse(plan["caller_truth_values_accepted"])
        self.assertEqual(plan["source_artifact_count"], 7)

    def test_fresh_stopped_receipt_derives_exact_claims_and_seven_sources(self):
        closure = self.fixture.closure()
        self.assertEqual(
            closure["claims"],
            {
                "redis_target_count": 3,
                "unsafe_redis_path_count": 0,
                "nonempty_redis_target_count": 0,
                "legacy_redis_restore_byte_count": 0,
            },
        )
        self.assertEqual(
            set(closure["source_artifact_inventory"]),
            {"aggregate", *MODULE.ROLES},
        )
        self.assertTrue(
            closure["source_artifacts_stable_readback_verified"]
        )
        self.assertFalse(closure["caller_truth_values_accepted"])
        self.assertFalse(closure["redis_mutated"])

    def _loaded(self, *, now: datetime | None = None) -> dict:
        return PREPARED.load_pre_freeze_current_operation_receipt(
            self.fixture.spec.receipt_path,
            output_root=self.fixture.output_root,
            now=now or BASE + timedelta(seconds=10),
        )

    def test_nonempty_unsafe_and_cross_role_substitution_fail_closed(self):
        mutations = ("nonempty", "unsafe", "cross-role")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                loaded = copy.deepcopy(self._loaded())
                if mutation == "nonempty":
                    loaded["responses"]["bot_fi"][
                        "prepared_redis_entry_count"
                    ] = 1
                elif mutation == "unsafe":
                    loaded["responses"]["webapp_fi"][
                        "prepared_redis_unsafe_path_count"
                    ] = 1
                else:
                    loaded["responses"]["webapp_ir"][
                        "prepared_redis_identity_sha256"
                    ] = loaded["responses"]["bot_fi"][
                        "prepared_redis_identity_sha256"
                    ]
                with (
                    self.fixture.patch_root(),
                    mock.patch.object(
                        MODULE,
                        "_validate_final_snapshot_source_closure",
                        return_value={
                            "source_closure_sha256": "c" * 64,
                            "aggregate_artifact_sha256": "d" * 64,
                            "legacy_redis_exclusion_sha256": "e" * 64,
                        },
                    ),
                    self.assertRaises(
                        MODULE.PristineRedisPhaseError
                    ),
                ):
                    MODULE._validate_loaded_receipt(
                        self.fixture.context,
                        self.fixture.spec,
                        loaded,
                        now=BASE + timedelta(seconds=10),
                    )

    def test_wrong_challenge_and_path_substitution_fail_closed(self):
        wrong_challenge = replace(
            self.fixture.spec,
            controller_challenge_sha256="7" * 64,
        )
        with (
            self.fixture.patch_root(),
            self.assertRaises(MODULE.PristineRedisPhaseError),
        ):
            MODULE.load_pristine_closure(
                self.fixture.context,
                wrong_challenge,
                now=BASE + timedelta(seconds=10),
            )
        wrong_path = replace(
            self.fixture.spec,
            receipt_path=self.fixture.spec.receipt_path.with_name(
                "substituted.json"
            ),
        )
        with (
            self.fixture.patch_root(),
            self.assertRaises(MODULE.PristineRedisPhaseError),
        ):
            MODULE.load_pristine_closure(
                self.fixture.context,
                wrong_path,
                now=BASE + timedelta(seconds=10),
            )

    def test_copied_receipt_package_outside_manifest_root_is_rejected(self):
        copied_root = self.root / "copied"
        copied_parent = (
            copied_root
            / "prepared-clone-inventory"
            / self.fixture.manifest["operation_id"]
            / self.fixture.spec.controller_challenge_sha256
        )
        copied_parent.parent.mkdir(mode=0o700, parents=True)
        shutil.copytree(
            self.fixture.spec.receipt_path.parent,
            copied_parent,
        )
        for directory in (
            copied_root,
            copied_root / "prepared-clone-inventory",
            copied_parent.parent,
            copied_parent,
        ):
            directory.chmod(0o700)
        for path in copied_parent.iterdir():
            path.chmod(0o600)
        copied_spec = replace(
            self.fixture.spec,
            receipt_path=(
                copied_parent
                / PREPARED.PRE_FREEZE_CURRENT_OPERATION_RECEIPT_FILENAME
            ),
        )
        with (
            self.fixture.patch_root(),
            self.assertRaises(MODULE.PristineRedisPhaseError),
        ):
            MODULE.load_pristine_closure(
                self.fixture.context,
                copied_spec,
                now=BASE + timedelta(seconds=10),
            )

    def test_expired_receipt_and_content_mutation_fail_closed(self):
        with (
            self.fixture.patch_root(),
            self.assertRaises(MODULE.PristineRedisPhaseError),
        ):
            MODULE.load_pristine_closure(
                self.fixture.context,
                self.fixture.spec,
                now=BASE + timedelta(minutes=3),
            )
        request_path = (
            self.fixture.spec.receipt_path.parent
            / PREPARED.REQUEST_FILENAMES["bot_fi"]
        )
        original = request_path.read_bytes()
        request_path.write_bytes(original[:-2] + b" }")
        os.chmod(request_path, 0o600)
        with (
            self.fixture.patch_root(),
            self.assertRaises(MODULE.PristineRedisPhaseError),
        ):
            MODULE.load_pristine_closure(
                self.fixture.context,
                self.fixture.spec,
                now=BASE + timedelta(seconds=10),
            )

    def test_same_byte_touch_is_not_used_as_origin_authority(self):
        aggregate = self.fixture.spec.receipt_path
        metadata = aggregate.stat()
        os.utime(
            aggregate,
            ns=(
                metadata.st_atime_ns,
                metadata.st_mtime_ns + 30_000_000_000,
            ),
        )
        closure = self.fixture.closure()
        self.assertTrue(
            closure["source_artifacts_stable_readback_verified"]
        )

    def test_publication_is_create_only_and_passes_semantic_verifier(self):
        closure = self.fixture.closure()
        _spec, binding = MODULE._source_spec_binding(
            self.fixture.spec
        )
        with self.fixture.patch_root():
            first = MODULE.publish_phase_evidence(
                self.fixture.context,
                closure=closure,
                source_binding_sha256=binding,
                now=BASE + timedelta(seconds=11),
            )
            second = MODULE.publish_phase_evidence(
                self.fixture.context,
                closure=closure,
                source_binding_sha256=binding,
                now=BASE + timedelta(seconds=11),
            )
        self.assertEqual(first, second)
        self.assertEqual(set(first), MODULE.PUBLICATION_FIELDS)
        self.assertFalse(first["production_contacted"])
        evidence, digest = VERIFY.read_root_only_evidence(
            Path(first["phase_evidence_path"])
        )
        self.assertEqual(digest, first["phase_evidence_sha256"])
        self.assertEqual(evidence["phase"], MODULE.PHASE)
        self.assertEqual(evidence["claims"]["redis_target_count"]["value"], 3)

    def test_publication_rejects_caller_claim_and_root_substitution(self):
        closure = self.fixture.closure()
        closure["claims"]["redis_target_count"] = 4
        closure["closure_sha256"] = hashlib.sha256(
            canonical_json(
                {
                    key: value
                    for key, value in closure.items()
                    if key != "closure_sha256"
                }
            )
        ).hexdigest()
        with (
            self.fixture.patch_root(),
            self.assertRaises(MODULE.PristineRedisPhaseError),
        ):
            MODULE.publish_phase_evidence(
                self.fixture.context,
                closure=closure,
                source_binding_sha256="f" * 64,
                now=BASE + timedelta(seconds=11),
            )
        substituted = replace(
            self.fixture.context,
            output_root=self.root / "other-root",
        )
        substituted.output_root.mkdir(mode=0o700)
        with self.assertRaises(MODULE.PristineRedisPhaseError):
            MODULE.validate_closure(
                substituted,
                self.fixture.closure(),
            )

    def test_final_snapshot_source_failure_blocks_restore_byte_claim(self):
        loaded = self._loaded()
        with (
            self.fixture.patch_root(),
            mock.patch.object(
                MODULE,
                "_validate_final_snapshot_source_closure",
                side_effect=MODULE.PristineRedisPhaseError(
                    "restore artifact included"
                ),
            ),
            self.assertRaisesRegex(
                MODULE.PristineRedisPhaseError,
                "restore artifact included",
            ),
        ):
            MODULE._validate_loaded_receipt(
                self.fixture.context,
                self.fixture.spec,
                loaded,
                now=BASE + timedelta(seconds=10),
            )

    def test_final_snapshot_closure_excludes_nonzero_rollback_redis(self):
        source_root = self.root / "frozen-source"
        source_root.mkdir(mode=0o700)
        sources = FROZEN_FIXTURE.validated_sources(source_root)
        derived = MODULE.FROZEN_PHASE._phase_claims(
            MODULE.PRIOR_PHASE,
            sources,
        )
        prior_records = copy.deepcopy(
            dict(self.fixture.context.prior_records)
        )
        final_evidence = prior_records[MODULE.PRIOR_PHASE]
        for claim, value in derived.items():
            final_evidence["claims"][claim]["value"] = value
        operation = next(
            item.operation
            for item in CONTROLLER.PHASE_SPECS
            if item.phase == MODULE.PRIOR_PHASE
        )
        role_requests: dict[str, str] = {}
        role_sources: dict[str, str] = {}
        role_observed = {
            role: final_evidence["captured_at"]
            for role in MODULE.FROZEN_PHASE.ROLE_ORDER
        }
        for row in final_evidence["role_attestations"]:
            role = row["role"]
            source = sources.records[f"{role}_snapshot_manifest"]
            request = MODULE.FROZEN_PHASE._aggregate_hash(
                {
                    "phase": MODULE.PRIOR_PHASE,
                    "operation": operation,
                    "role": role,
                    "source_path": os.fspath(source.path),
                    "source_sha256": source.sha256,
                    "source_closure_sha256": (
                        sources.source_closure_sha256
                    ),
                }
            )
            row["request_sha256"] = request
            role_requests[role] = request
            role_sources[role] = row["source_artifact_sha256"]
        request_record = sources.records[
            "current_frozen_verification_receipt"
        ]
        frozen_context = SimpleNamespace(
            request=request_record,
            manifest_path=self.fixture.context.manifest_path,
            manifest=self.fixture.context.manifest,
            manifest_sha256=self.fixture.context.manifest_sha256,
            plan_sha256=self.fixture.context.plan_sha256,
            output_root=(
                self.fixture.context.output_root
                / "freeze-snapshot-phase-bridge"
            ),
        )
        aggregate = {
            "schema": MODULE.FROZEN_PHASE.PHASE_AGGREGATE_SCHEMA,
            "status": "completed",
            "campaign_id": self.fixture.manifest["campaign_id"],
            "operation_id": self.fixture.manifest["operation_id"],
            "release_sha": self.fixture.manifest["release_sha"],
            "release_tree_sha": self.fixture.manifest[
                "release_tree_sha"
            ],
            "manifest_sha256": self.fixture.context.manifest_sha256,
            "plan_sha256": self.fixture.context.plan_sha256,
            "phase": MODULE.PRIOR_PHASE,
            "operation": operation,
            "roles": list(MODULE.FROZEN_PHASE.ROLE_ORDER),
            "source_closure_sha256": sources.source_closure_sha256,
            "claims": derived,
            "phase_evidence_path": os.fspath(
                self.fixture.context.prior_paths[MODULE.PRIOR_PHASE]
            ),
            "phase_evidence_sha256": self.fixture.context.prior_digests[
                MODULE.PRIOR_PHASE
            ],
            "caller_truth_values_accepted": False,
            "legacy_writers_frozen": True,
            "restore_performed": False,
            "writer_restart_performed": False,
            "business_write_observed": False,
        }
        aggregate_payload = canonical_json(aggregate) + b"\n"
        aggregate_digest = hashlib.sha256(
            aggregate_payload
        ).hexdigest()
        aggregate_path = (
            frozen_context.output_root
            / MODULE.PRIOR_PHASE
            / "aggregates"
            / (
                f"phase-aggregate-{MODULE.PRIOR_PHASE}."
                f"{aggregate_digest}.json"
            )
        )
        spec = replace(
            self.fixture.spec,
            final_snapshot_request_path=request_record.path,
            final_snapshot_request_sha256=request_record.sha256,
            final_snapshot_aggregate_path=aggregate_path,
            final_snapshot_aggregate_sha256=aggregate_digest,
        )
        aggregate_path.parent.mkdir(mode=0o700, parents=True)
        aggregate_path.write_bytes(aggregate_payload)
        aggregate_path.chmod(0o600)
        context = replace(
            self.fixture.context,
            prior_records=prior_records,
        )
        dynamic = {
            name: value
            for name, value in derived.items()
            if VERIFY.PHASE_CLAIM_RULES[MODULE.PRIOR_PHASE][name].kind
            != "exact"
        }
        claim_sources = {
            claim: final_evidence["claims"][claim]["source_sha256"]
            for claim in derived
        }
        with (
            mock.patch.object(
                MODULE.FROZEN_PHASE,
                "_load_request",
                return_value=frozen_context,
            ),
            mock.patch.object(
                MODULE.FROZEN_PHASE,
                "_validate_sources",
                return_value=sources,
            ),
            mock.patch.object(
                MODULE.VERIFY,
                "_read_role_validation_records",
                return_value=(
                    role_requests,
                    role_sources,
                    role_observed,
                ),
            ),
            mock.patch.object(
                MODULE.VERIFY,
                "_read_claim_source_records",
                return_value=(dynamic, claim_sources),
            ),
            mock.patch.object(
                MODULE.VERIFY,
                "verify_phase_evidence",
                return_value={"status": "verified"},
            ),
            mock.patch.object(
                MODULE.FROZEN_PHASE,
                "_assert_records_unchanged",
            ),
        ):
            result = MODULE._validate_final_snapshot_source_closure(
                context,
                spec,
                now=BASE + timedelta(seconds=10),
            )
        self.assertEqual(
            result["source_closure_sha256"],
            sources.source_closure_sha256,
        )
        self.assertNotEqual(
            result["legacy_redis_exclusion_sha256"],
            MODULE.ZERO_SHA256,
        )
        self.assertTrue(
            all(
                snapshot["redis_rollback_only"]["bytes"] > 0
                for snapshot in sources.snapshots.values()
            )
        )

        bad_snapshots = copy.deepcopy(dict(sources.snapshots))
        bad_snapshots["webapp_fi"]["redis_rollback_only"][
            "restore"
        ] = True
        bad_sources = replace(sources, snapshots=bad_snapshots)
        with (
            mock.patch.object(
                MODULE.FROZEN_PHASE,
                "_load_request",
                return_value=frozen_context,
            ),
            mock.patch.object(
                MODULE.FROZEN_PHASE,
                "_validate_sources",
                return_value=bad_sources,
            ),
            self.assertRaisesRegex(
                MODULE.PristineRedisPhaseError,
                "source closure is invalid",
            ),
        ):
            MODULE._validate_final_snapshot_source_closure(
                context,
                spec,
                now=BASE + timedelta(seconds=10),
            )

    def test_request_loader_contains_references_but_no_claim_truth(self):
        request = {
            "schema": MODULE.REQUEST_SCHEMA,
            "status": "ready",
            "campaign_id": self.fixture.manifest["campaign_id"],
            "operation_id": self.fixture.manifest["operation_id"],
            "release_sha": self.fixture.manifest["release_sha"],
            "release_tree_sha": self.fixture.manifest[
                "release_tree_sha"
            ],
            "manifest_path": os.fspath(
                self.fixture.context.manifest_path
            ),
            "manifest_sha256": self.fixture.context.manifest_sha256,
            "approval_path": os.fspath(
                self.fixture.context.approval_path
            ),
            "approval_sha256": self.fixture.manifest["artifacts"][
                "cutover_approval_sha256"
            ],
            "approval_policy_path": os.fspath(
                self.fixture.context.approval_policy_path
            ),
            "approval_policy_sha256": self.fixture.manifest["artifacts"][
                "human_approval_policy_sha256"
            ],
            "prior_phase_evidence": {
                phase: {
                    "path": os.fspath(
                        self.fixture.context.prior_paths[phase]
                    ),
                    "sha256": self.fixture.context.prior_digests[phase],
                }
                for phase in MODULE._prior_phase_names()
            },
            "prepared_inventory_receipt": {
                "path": os.fspath(self.fixture.spec.receipt_path),
                "sha256": (
                    self.fixture.spec.aggregate_artifact_sha256
                ),
                "controller_challenge_sha256": (
                    self.fixture.spec.controller_challenge_sha256
                ),
            },
            "final_snapshot_request": {
                "path": os.fspath(
                    self.fixture.spec.final_snapshot_request_path
                ),
                "sha256": (
                    self.fixture.spec.final_snapshot_request_sha256
                ),
            },
            "final_snapshot_aggregate": {
                "path": os.fspath(
                    self.fixture.spec.final_snapshot_aggregate_path
                ),
                "sha256": (
                    self.fixture.spec.final_snapshot_aggregate_sha256
                ),
            },
            "constraints": dict(MODULE.EXPECTED_REQUEST_CONSTRAINTS),
        }
        request_path = self.root / "phase-request.json"
        request_path.write_bytes(canonical_json(request) + b"\n")
        request_path.chmod(0o600)
        with mock.patch.object(
            MODULE,
            "load_evidence_context",
            return_value=self.fixture.context,
        ):
            context, spec, request_sha256 = MODULE.load_phase_request(
                request_path
            )
        self.assertEqual(context, self.fixture.context)
        self.assertEqual(spec, self.fixture.spec)
        self.assertNotEqual(request_sha256, MODULE.ZERO_SHA256)
        self.assertFalse(any(claim in request for claim in MODULE.CLAIMS))

    def test_begin_request_loader_contains_context_only(self):
        request = {
            "schema": MODULE.BEGIN_REQUEST_SCHEMA,
            "status": "ready",
            "campaign_id": self.fixture.manifest["campaign_id"],
            "operation_id": self.fixture.manifest["operation_id"],
            "release_sha": self.fixture.manifest["release_sha"],
            "release_tree_sha": self.fixture.manifest[
                "release_tree_sha"
            ],
            "manifest_path": os.fspath(
                self.fixture.context.manifest_path
            ),
            "manifest_sha256": self.fixture.context.manifest_sha256,
            "approval_path": os.fspath(
                self.fixture.context.approval_path
            ),
            "approval_sha256": self.fixture.manifest["artifacts"][
                "cutover_approval_sha256"
            ],
            "approval_policy_path": os.fspath(
                self.fixture.context.approval_policy_path
            ),
            "approval_policy_sha256": self.fixture.manifest["artifacts"][
                "human_approval_policy_sha256"
            ],
            "prior_phase_evidence": {
                phase: {
                    "path": os.fspath(
                        self.fixture.context.prior_paths[phase]
                    ),
                    "sha256": self.fixture.context.prior_digests[phase],
                }
                for phase in MODULE._prior_phase_names()
            },
            "constraints": dict(
                MODULE.EXPECTED_BEGIN_REQUEST_CONSTRAINTS
            ),
        }
        request_path = self.root / "capture-begin-request.json"
        request_path.write_bytes(canonical_json(request) + b"\n")
        request_path.chmod(0o600)
        with mock.patch.object(
            MODULE,
            "load_evidence_context",
            return_value=self.fixture.context,
        ):
            context, request_sha256 = MODULE.load_begin_request(
                request_path
            )
        self.assertEqual(context, self.fixture.context)
        self.assertNotEqual(request_sha256, MODULE.ZERO_SHA256)
        self.assertFalse(
            any(
                field in request
                for field in (
                    "prepared_inventory_receipt",
                    "final_snapshot_request",
                    "final_snapshot_aggregate",
                    *MODULE.CLAIMS,
                )
            )
        )

    def test_capture_begin_is_live_authorized_and_idempotent(self):
        fixture = Fixture(
            self.root / "capture-begin",
            start_phase=False,
            publish_receipt=False,
        )
        request_sha256 = "f" * 64
        authorization = mock.Mock()
        with (
            fixture.patch_root(),
            mock.patch.object(
                CONTROLLER,
                "_now",
                return_value=BASE.isoformat(),
            ),
        ):
            plan = MODULE.build_begin_capture_plan(
                fixture.context,
                request_sha256=request_sha256,
            )
            result = MODULE.begin_capture_phase(
                fixture.context,
                request_sha256=request_sha256,
                confirm=plan["required_confirmation"],
                control_fd=7,
                journal_factory=lambda _path: fixture.journal,
                liveness_factory=live_context,
                signal_authority_factory=lambda: mock.MagicMock(
                    __enter__=mock.Mock(return_value=None),
                    __exit__=mock.Mock(return_value=False),
                ),
                authorization_verifier=authorization,
            )
        self.assertEqual(result["status"], "capture-required")
        self.assertTrue(result["journal_mutated"])
        self.assertEqual(
            result["capture_binding"]["journal_started_at"],
            BASE.isoformat(),
        )
        self.assertFalse(result["capture_binding"]["claim_values_included"])
        self.assertFalse(result["production_contacted"])
        self.assertFalse(result["redis_mutated"])

        resumed = replace(
            fixture.context,
            journal=fixture.journal.load(),
        )
        with fixture.patch_root():
            retry = MODULE.begin_capture_phase(
                resumed,
                request_sha256=request_sha256,
                confirm=plan["required_confirmation"],
                control_fd=7,
                journal_factory=lambda _path: fixture.journal,
                liveness_factory=live_context,
                signal_authority_factory=lambda: mock.MagicMock(
                    __enter__=mock.Mock(return_value=None),
                    __exit__=mock.Mock(return_value=False),
                ),
                authorization_verifier=authorization,
            )
        self.assertEqual(retry["status"], "capture-required-reused")
        self.assertFalse(retry["journal_mutated"])
        self.assertEqual(
            retry["capture_binding_sha256"],
            result["capture_binding_sha256"],
        )

    def test_capture_begin_never_reports_unverified_completed_state(self):
        fixture = Fixture(
            self.root / "begin-completed",
            publish_receipt=False,
        )
        fixture.journal.complete_phase(
            MODULE.PHASE,
            verification=CONTROLLER.VerifiedPhaseCompletion(
                phase=MODULE.PHASE,
                evidence_sha256="a" * 64,
                receipt_sha256="b" * 64,
            ),
        )
        completed_context = replace(
            fixture.context,
            journal=fixture.journal.load(),
        )
        with (
            fixture.patch_root(),
            self.assertRaisesRegex(
                MODULE.PristineRedisPhaseError,
                "completion readback",
            ),
        ):
            plan = MODULE.build_begin_capture_plan(
                completed_context,
                request_sha256="c" * 64,
            )
            MODULE.begin_capture_phase(
                completed_context,
                request_sha256="c" * 64,
                confirm=plan["required_confirmation"],
                control_fd=7,
                journal_factory=lambda _path: fixture.journal,
                liveness_factory=live_context,
                signal_authority_factory=lambda: mock.MagicMock(
                    __enter__=mock.Mock(return_value=None),
                    __exit__=mock.Mock(return_value=False),
                ),
                authorization_verifier=lambda _context: None,
            )

    def test_capture_begin_auth_or_liveness_failure_cannot_mutate_journal(
        self,
    ):
        for failure in ("authorization", "liveness"):
            with self.subTest(failure=failure):
                fixture = Fixture(
                    self.root / f"begin-blocked-{failure}",
                    start_phase=False,
                    publish_receipt=False,
                )
                authorization = (
                    mock.Mock(
                        side_effect=MODULE.PristineRedisPhaseError(
                            "expired"
                        )
                    )
                    if failure == "authorization"
                    else mock.Mock()
                )
                liveness = (
                    lost_live_context
                    if failure == "liveness"
                    else live_context
                )
                with (
                    fixture.patch_root(),
                    self.assertRaises(MODULE.PristineRedisPhaseError),
                ):
                    plan = MODULE.build_begin_capture_plan(
                        fixture.context,
                        request_sha256="e" * 64,
                    )
                    MODULE.begin_capture_phase(
                        fixture.context,
                        request_sha256="e" * 64,
                        confirm=plan["required_confirmation"],
                        control_fd=7,
                        journal_factory=lambda _path: fixture.journal,
                        liveness_factory=liveness,
                        signal_authority_factory=lambda: mock.MagicMock(
                            __enter__=mock.Mock(return_value=None),
                            __exit__=mock.Mock(return_value=False),
                        ),
                        authorization_verifier=authorization,
                    )
                state = fixture.journal.load()
                self.assertEqual(state["status"], "active")
                self.assertIsNone(state["started_phase"])

    def test_first_run_begins_then_captures_then_applies(self):
        fixture = Fixture(
            self.root / "chronology",
            start_phase=False,
            publish_receipt=False,
        )
        with (
            fixture.patch_root(),
            mock.patch.object(
                CONTROLLER,
                "_now",
                return_value=BASE.isoformat(),
            ),
        ):
            begin_plan = MODULE.build_begin_capture_plan(
                fixture.context,
                request_sha256="d" * 64,
            )
            begin = MODULE.begin_capture_phase(
                fixture.context,
                request_sha256="d" * 64,
                confirm=begin_plan["required_confirmation"],
                control_fd=7,
                journal_factory=lambda _path: fixture.journal,
                liveness_factory=live_context,
                signal_authority_factory=lambda: mock.MagicMock(
                    __enter__=mock.Mock(return_value=None),
                    __exit__=mock.Mock(return_value=False),
                ),
                authorization_verifier=lambda _context: None,
            )
        self.assertEqual(begin["status"], "capture-required")

        started_context = replace(
            fixture.context,
            journal=fixture.journal.load(),
        )
        spec = fixture._publish_stopped_receipt(
            challenge="a" * 64,
            issued_at=BASE + timedelta(seconds=2),
        )
        _source, source_binding = MODULE._source_spec_binding(spec)
        plan = MODULE.build_plan(
            operation_id=fixture.manifest["operation_id"],
            release_sha=fixture.manifest["release_sha"],
            source_available=True,
            manifest_sha256=fixture.context.manifest_sha256,
            controller_plan_sha256=fixture.context.plan_sha256,
            source_binding_sha256=source_binding,
        )
        publication = {
            "phase_evidence_path": os.fspath(
                self.root / "chronology-evidence.json"
            ),
            "phase_evidence_sha256": "b" * 64,
            "role_validation_paths": {
                role: os.fspath(
                    self.root / f"chronology-{role}.json"
                )
                for role in MODULE.ROLES
            },
            "claim_source_paths": {
                claim: os.fspath(
                    self.root / f"chronology-{claim}.json"
                )
                for claim in MODULE.CLAIMS
            },
        }
        receipt = b"chronology-verification-receipt"
        token = CONTROLLER.VerifiedPhaseCompletion(
            phase=MODULE.PHASE,
            evidence_sha256="b" * 64,
            receipt_sha256=hashlib.sha256(receipt).hexdigest(),
        )
        with (
            fixture.patch_root(),
            mock.patch.object(
                MODULE,
                "_validate_final_snapshot_source_closure",
                return_value={
                    "source_closure_sha256": "c" * 64,
                    "aggregate_artifact_sha256": "d" * 64,
                    "legacy_redis_exclusion_sha256": "e" * 64,
                },
            ),
            mock.patch.object(
                MODULE,
                "load_pristine_closure",
                wraps=MODULE.load_pristine_closure,
            ) as load_source,
            mock.patch.object(
                MODULE,
                "publish_phase_evidence",
                return_value=publication,
            ),
            mock.patch.object(
                MODULE,
                "_load_publication_index",
                side_effect=[None, publication],
            ),
            mock.patch.object(MODULE, "_persist_publication_index"),
            mock.patch.object(
                MODULE,
                "_load_verification_candidate",
                return_value=(token, receipt),
            ),
        ):
            result = MODULE.apply_phase(
                started_context,
                source_spec=spec,
                confirm=plan["required_confirmation"],
                control_fd=7,
                now=BASE + timedelta(seconds=11),
                journal_factory=lambda _path: fixture.journal,
                liveness_factory=live_context,
                signal_authority_factory=lambda: mock.MagicMock(
                    __enter__=mock.Mock(return_value=None),
                    __exit__=mock.Mock(return_value=False),
                ),
                authorization_verifier=lambda _context: None,
                receipt_persister=lambda **_kwargs: self.root
                / "chronology-receipt.json",
            )
        self.assertEqual(load_source.call_count, 1)
        self.assertEqual(result["status"], "completed")
        self.assertIn(MODULE.PHASE, fixture.journal.load()["completed_phases"])

    def test_apply_requires_authorization_liveness_and_completes(self):
        fixture = Fixture(self.root / "started")
        closure = self.fixture.closure()
        publication = {
            "phase_evidence_path": os.fspath(self.root / "evidence.json"),
            "phase_evidence_sha256": "1" * 64,
            "role_validation_paths": {
                role: os.fspath(self.root / f"{role}.json")
                for role in MODULE.ROLES
            },
            "claim_source_paths": {
                claim: os.fspath(self.root / f"{claim}.json")
                for claim in MODULE.CLAIMS
            },
        }
        receipt = b"release-verification-receipt"
        token = CONTROLLER.VerifiedPhaseCompletion(
            phase=MODULE.PHASE,
            evidence_sha256="1" * 64,
            receipt_sha256=hashlib.sha256(receipt).hexdigest(),
        )
        authorization = mock.Mock()
        with (
            fixture.patch_root(),
            mock.patch.object(
                MODULE,
                "load_pristine_closure",
                return_value=closure,
            ),
            mock.patch.object(
                MODULE,
                "publish_phase_evidence",
                return_value=publication,
            ),
            mock.patch.object(
                MODULE,
                "_load_publication_index",
                return_value=publication,
            ),
            mock.patch.object(
                MODULE,
                "_load_verification_candidate",
                return_value=(token, receipt),
            ),
        ):
            _spec, binding = MODULE._source_spec_binding(fixture.spec)
            plan = MODULE.build_plan(
                operation_id=fixture.manifest["operation_id"],
                release_sha=fixture.manifest["release_sha"],
                source_available=True,
                manifest_sha256=fixture.context.manifest_sha256,
                controller_plan_sha256=fixture.context.plan_sha256,
                source_binding_sha256=binding,
            )
            result = MODULE.apply_phase(
                fixture.context,
                source_spec=fixture.spec,
                confirm=plan["required_confirmation"],
                control_fd=7,
                now=BASE + timedelta(seconds=11),
                journal_factory=lambda _path: fixture.journal,
                liveness_factory=live_context,
                signal_authority_factory=lambda: mock.MagicMock(
                    __enter__=mock.Mock(return_value=None),
                    __exit__=mock.Mock(return_value=False),
                ),
                authorization_verifier=authorization,
                receipt_persister=lambda **_kwargs: self.root
                / "receipt.json",
            )
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["production_contacted"])
        self.assertFalse(result["redis_mutated"])
        self.assertGreaterEqual(authorization.call_count, 4)
        self.assertIn(MODULE.PHASE, fixture.journal.load()["completed_phases"])

    def test_apply_from_active_fails_without_journal_mutation(self):
        fixture = Fixture(
            self.root / "apply-before-begin",
            start_phase=False,
        )
        _spec, binding = MODULE._source_spec_binding(fixture.spec)
        with fixture.patch_root():
            plan = MODULE.build_plan(
                operation_id=fixture.manifest["operation_id"],
                release_sha=fixture.manifest["release_sha"],
                source_available=True,
                manifest_sha256=fixture.context.manifest_sha256,
                controller_plan_sha256=fixture.context.plan_sha256,
                source_binding_sha256=binding,
            )
        source_loader = mock.Mock()
        with (
            fixture.patch_root(),
            mock.patch.object(
                MODULE,
                "load_pristine_closure",
                source_loader,
            ),
            self.assertRaisesRegex(
                MODULE.PristineRedisPhaseError,
                "exact journal successor",
            ),
        ):
            MODULE.apply_phase(
                fixture.context,
                source_spec=fixture.spec,
                confirm=plan["required_confirmation"],
                control_fd=7,
                journal_factory=lambda _path: fixture.journal,
                liveness_factory=live_context,
                signal_authority_factory=lambda: mock.MagicMock(
                    __enter__=mock.Mock(return_value=None),
                    __exit__=mock.Mock(return_value=False),
                ),
                authorization_verifier=lambda _context: None,
            )
        state = fixture.journal.load()
        self.assertEqual(state["status"], "active")
        self.assertIsNone(state["started_phase"])
        source_loader.assert_not_called()

    def test_crash_after_publication_resumes_without_reloading_source(self):
        fixture = Fixture(self.root / "resume")
        closure = self.fixture.closure()
        publication = {
            "phase_evidence_path": os.fspath(self.root / "resume-evidence.json"),
            "phase_evidence_sha256": "2" * 64,
            "role_validation_paths": {
                role: os.fspath(self.root / f"resume-{role}.json")
                for role in MODULE.ROLES
            },
            "claim_source_paths": {
                claim: os.fspath(self.root / f"resume-{claim}.json")
                for claim in MODULE.CLAIMS
            },
        }
        receipt = b"resumed-verification-receipt"
        token = CONTROLLER.VerifiedPhaseCompletion(
            phase=MODULE.PHASE,
            evidence_sha256="2" * 64,
            receipt_sha256=hashlib.sha256(receipt).hexdigest(),
        )
        _spec, binding = MODULE._source_spec_binding(fixture.spec)
        plan = MODULE.build_plan(
            operation_id=fixture.manifest["operation_id"],
            release_sha=fixture.manifest["release_sha"],
            source_available=True,
            manifest_sha256=fixture.context.manifest_sha256,
            controller_plan_sha256=fixture.context.plan_sha256,
            source_binding_sha256=binding,
        )
        load_source = mock.Mock(return_value=closure)
        with (
            fixture.patch_root(),
            mock.patch.object(
                MODULE,
                "load_pristine_closure",
                load_source,
            ),
            mock.patch.object(
                MODULE,
                "publish_phase_evidence",
                return_value=publication,
            ),
            mock.patch.object(
                MODULE,
                "_load_publication_index",
                side_effect=[None, publication],
            ),
            mock.patch.object(
                MODULE,
                "_persist_publication_index",
                side_effect=RuntimeError("simulated crash"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                MODULE.apply_phase(
                    fixture.context,
                    source_spec=fixture.spec,
                    confirm=plan["required_confirmation"],
                    control_fd=7,
                    journal_factory=lambda _path: fixture.journal,
                    liveness_factory=live_context,
                    signal_authority_factory=lambda: mock.MagicMock(
                        __enter__=mock.Mock(return_value=None),
                        __exit__=mock.Mock(return_value=False),
                    ),
                    authorization_verifier=lambda _context: None,
                )
        resumed_context = replace(
            fixture.context,
            journal=fixture.journal.load(),
        )
        with (
            fixture.patch_root(),
            mock.patch.object(
                MODULE,
                "load_pristine_closure",
                load_source,
            ),
            mock.patch.object(
                MODULE,
                "_load_publication_index",
                return_value=publication,
            ),
            mock.patch.object(
                MODULE,
                "_load_verification_candidate",
                return_value=(token, receipt),
            ),
        ):
            result = MODULE.apply_phase(
                resumed_context,
                source_spec=fixture.spec,
                confirm=plan["required_confirmation"],
                control_fd=7,
                journal_factory=lambda _path: fixture.journal,
                liveness_factory=live_context,
                signal_authority_factory=lambda: mock.MagicMock(
                    __enter__=mock.Mock(return_value=None),
                    __exit__=mock.Mock(return_value=False),
                ),
                authorization_verifier=lambda _context: None,
                receipt_persister=lambda **_kwargs: self.root
                / "resumed-receipt.json",
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(load_source.call_count, 1)

    def test_verification_candidate_resumes_without_rerunning_verifier(self):
        fixture = Fixture(self.root / "candidate")
        publication = {
            "phase_evidence_path": os.fspath(
                self.root / "candidate-evidence.json"
            ),
            "phase_evidence_sha256": "4" * 64,
            "role_validation_paths": {
                role: os.fspath(self.root / f"candidate-{role}.json")
                for role in MODULE.ROLES
            },
            "claim_source_paths": {
                claim: os.fspath(
                    self.root / f"candidate-{claim}.json"
                )
                for claim in MODULE.CLAIMS
            },
        }
        receipt = b"durable-candidate-receipt"
        token = CONTROLLER.VerifiedPhaseCompletion(
            phase=MODULE.PHASE,
            evidence_sha256="4" * 64,
            receipt_sha256=hashlib.sha256(receipt).hexdigest(),
        )
        _spec, binding = MODULE._source_spec_binding(fixture.spec)
        plan = MODULE.build_plan(
            operation_id=fixture.manifest["operation_id"],
            release_sha=fixture.manifest["release_sha"],
            source_available=True,
            manifest_sha256=fixture.context.manifest_sha256,
            controller_plan_sha256=fixture.context.plan_sha256,
            source_binding_sha256=binding,
        )
        release_verifier = mock.Mock(return_value=(token, receipt))
        candidate_values = [None, (token, receipt)]
        with (
            fixture.patch_root(),
            mock.patch.object(
                MODULE,
                "_load_publication_index",
                return_value=publication,
            ),
            mock.patch.object(
                MODULE,
                "_load_verification_candidate",
                side_effect=candidate_values,
            ),
            mock.patch.object(MODULE, "_persist_fixed_bytes"),
            self.assertRaisesRegex(RuntimeError, "after candidate"),
        ):
            MODULE.apply_phase(
                fixture.context,
                source_spec=fixture.spec,
                confirm=plan["required_confirmation"],
                control_fd=7,
                journal_factory=lambda _path: fixture.journal,
                liveness_factory=live_context,
                signal_authority_factory=lambda: mock.MagicMock(
                    __enter__=mock.Mock(return_value=None),
                    __exit__=mock.Mock(return_value=False),
                ),
                authorization_verifier=lambda _context: None,
                release_verifier=release_verifier,
                receipt_persister=mock.Mock(
                    side_effect=RuntimeError("after candidate")
                ),
            )
        self.assertEqual(release_verifier.call_count, 1)
        resumed = replace(
            fixture.context,
            journal=fixture.journal.load(),
        )
        with (
            fixture.patch_root(),
            mock.patch.object(
                MODULE,
                "_load_publication_index",
                return_value=publication,
            ),
            mock.patch.object(
                MODULE,
                "_load_verification_candidate",
                return_value=(token, receipt),
            ),
        ):
            result = MODULE.apply_phase(
                resumed,
                source_spec=fixture.spec,
                confirm=plan["required_confirmation"],
                control_fd=7,
                journal_factory=lambda _path: fixture.journal,
                liveness_factory=live_context,
                signal_authority_factory=lambda: mock.MagicMock(
                    __enter__=mock.Mock(return_value=None),
                    __exit__=mock.Mock(return_value=False),
                ),
                authorization_verifier=lambda _context: None,
                release_verifier=release_verifier,
                receipt_persister=lambda **_kwargs: self.root
                / "candidate-receipt.json",
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(release_verifier.call_count, 1)

    def test_main_routes_plan_and_apply_through_trusted_request_loader(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                expected = {
                    "status": "planned" if not apply else "completed"
                }
                argv = ["--request", os.fspath(self.root / "request.json")]
                if apply:
                    argv.extend(
                        [
                            "--apply",
                            "--confirm",
                            "confirmation",
                            "--controller-liveness-fd",
                            "8",
                        ]
                    )
                output = io.BytesIO()
                stdout = mock.Mock()
                stdout.buffer = output
                with (
                    mock.patch.object(
                        MODULE,
                        "_read_strict_document",
                        return_value=(
                            {"schema": MODULE.REQUEST_SCHEMA},
                            b"request",
                        ),
                    ),
                    mock.patch.object(
                        MODULE,
                        "load_phase_request",
                        return_value=(
                            self.fixture.context,
                            self.fixture.spec,
                            "f" * 64,
                        ),
                    ),
                    mock.patch.object(
                        MODULE,
                        "execute",
                        return_value=expected,
                    ) as execute,
                    mock.patch.object(MODULE.sys, "stdout", stdout),
                ):
                    status = MODULE.main(argv)
                self.assertEqual(status, 0)
                self.assertEqual(
                    json.loads(output.getvalue().decode("ascii")),
                    expected,
                )
                self.assertEqual(
                    execute.call_args.kwargs["control_fd"],
                    8 if apply else None,
                )

    def test_main_routes_capture_begin_plan_and_apply(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                expected = {
                    "status": (
                        "planned" if not apply else "capture-required"
                    )
                }
                argv = ["--request", os.fspath(self.root / "begin.json")]
                if apply:
                    argv.extend(
                        [
                            "--begin-capture",
                            "--confirm",
                            "confirmation",
                            "--controller-liveness-fd",
                            "9",
                        ]
                    )
                output = io.BytesIO()
                stdout = mock.Mock()
                stdout.buffer = output
                with (
                    mock.patch.object(
                        MODULE,
                        "_read_strict_document",
                        return_value=(
                            {"schema": MODULE.BEGIN_REQUEST_SCHEMA},
                            b"request",
                        ),
                    ),
                    mock.patch.object(
                        MODULE,
                        "load_begin_request",
                        return_value=(
                            self.fixture.context,
                            "e" * 64,
                        ),
                    ),
                    mock.patch.object(
                        MODULE,
                        "execute_begin_capture",
                        return_value=expected,
                    ) as execute_begin,
                    mock.patch.object(MODULE.sys, "stdout", stdout),
                ):
                    status = MODULE.main(argv)
                self.assertEqual(status, 0)
                self.assertEqual(
                    json.loads(output.getvalue().decode("ascii")),
                    expected,
                )
                self.assertEqual(
                    execute_begin.call_args.kwargs["control_fd"],
                    9 if apply else None,
                )


if __name__ == "__main__":
    unittest.main()
