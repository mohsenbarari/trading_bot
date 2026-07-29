from __future__ import annotations

from contextlib import redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
import time
import unittest
from unittest import mock

from scripts import orchestrate_production_shadow_pre_freeze_evidence as MODULE
from scripts import orchestrate_production_shadow_prepared_clone_inventory as PREPARED
from scripts import verify_production_shadow_phase_evidence as VERIFY
from tests import test_orchestrate_production_shadow_prepared_clone_inventory as PREPARED_FIXTURE
from tests.test_production_shadow_cutover_controller import manifest_payload


def canonical_json(value: object, *, newline: bool = True) -> bytes:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return payload + (b"\n" if newline else b"")


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def private_bytes(path: Path, payload: bytes) -> str:
    private_directory(path.parent)
    path.write_bytes(payload)
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


def private_json(
    path: Path,
    document: object,
    *,
    newline: bool = True,
) -> str:
    return private_bytes(
        path,
        canonical_json(document, newline=newline),
    )


class ExternalLivenessPipe:
    """Keep the liveness writer in a separate controller process."""

    def __init__(self) -> None:
        self.read_fd, writer_fd = os.pipe()
        command_read_fd, self._command_write_fd = os.pipe()
        self._pid = os.fork()
        if self._pid == 0:
            try:
                os.close(self.read_fd)
                os.close(self._command_write_fd)
                command = os.read(command_read_fd, 1)
                if command == b"d":
                    os.write(writer_fd, b"x")
            finally:
                os.close(command_read_fd)
                os.close(writer_fd)
                os._exit(0)
        os.close(writer_fd)
        os.close(command_read_fd)

    def close_writer(self, *, with_data: bool = False) -> None:
        if self._command_write_fd >= 0:
            os.write(
                self._command_write_fd,
                b"d" if with_data else b"e",
            )
            os.close(self._command_write_fd)
            self._command_write_fd = -1
        if self._pid > 0:
            waited, _status = os.waitpid(self._pid, 0)
            if waited != self._pid:
                raise AssertionError("liveness controller was not reaped")
            self._pid = -1

    def __enter__(self) -> "ExternalLivenessPipe":
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            self.close_writer()
        finally:
            os.close(self.read_fd)


class PreFreezeFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.manifest = copy.deepcopy(manifest_payload())
        self.manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ] = VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
        self.manifest["deployment"]["controller_evidence_root"] = os.fspath(
            root / "controller-evidence"
        )
        self.manifest["deployment"]["shadow_root"] = os.fspath(
            root / "shadow"
        )
        self.manifest_sha256 = "a" * 63 + "1"
        self.plan_sha256 = "b" * 63 + "2"
        self.context = MODULE.CoordinatorContext(
            manifest_path=root / "manifest.json",
            approval_path=root / "approval.json",
            approval_policy_path=root / "policy.json",
            manifest=self.manifest,
            manifest_sha256=self.manifest_sha256,
            plan={"phases": [], "plan_sha256": self.plan_sha256},
            plan_sha256=self.plan_sha256,
            output_root=root / "controller-evidence" / MODULE.PHASE,
            journal={},
        )
        self.values = self._claim_values()
        observed = self.now.isoformat()
        self.validated = MODULE.ValidatedInputs(
            context=self.context,
            records={},
            values=self.values,
            role_observed_at={
                role: observed for role in MODULE.ROLES
            },
            role_source_closure_sha256={
                role: hashlib.sha256(role.encode("ascii")).hexdigest()
                for role in MODULE.ROLES
            },
            claim_provenance=MODULE._claim_provenance(),
        )

    def _claim_values(self) -> dict[str, object]:
        values: dict[str, object] = {}
        bindings = VERIFY.PHASE_MANIFEST_CLAIM_BINDINGS[MODULE.PHASE]
        for claim, rule in VERIFY.PHASE_CLAIM_RULES[
            MODULE.PHASE
        ].items():
            if claim in bindings:
                values[claim] = VERIFY._manifest_artifact_binding_value(
                    self.manifest["artifacts"],
                    bindings[claim],
                )
            elif rule.kind == "exact":
                values[claim] = rule.expected
            elif claim == "active_route_generation_set_sha256":
                values[claim] = self.manifest["artifacts"][
                    "nginx_rollback_generation_sha256"
                ]
            else:
                raise AssertionError(f"unbound test claim: {claim}")
        return values

    def normalized_inputs(
        self,
    ) -> tuple[
        dict[str, Path],
        dict[str, str],
        dict[str, Path],
        dict[str, str],
    ]:
        role_paths: dict[str, Path] = {}
        role_digests: dict[str, str] = {}
        for index, role in enumerate(MODULE.ROLES, start=1):
            document = {
                "schema": MODULE.ROLE_VALIDATION_SCHEMA,
                "status": "validated-request",
                "request_sha256": str(index) * 64,
                "operation": MODULE.OPERATION,
                "role": role,
                "campaign_id": self.manifest["campaign_id"],
                "operation_id": self.manifest["operation_id"],
                "app_release_sha": self.manifest["release_sha"],
                "manifest_sha256": self.manifest_sha256,
                "approval_sha256": self.manifest["artifacts"][
                    "cutover_approval_sha256"
                ],
                "expected_host": self.manifest["topology"][role]["host"],
                "observed_host": self.manifest["topology"][role]["host"],
                "required_journal_status": (
                    MODULE.CONTROLLER.PRECOMMIT_JOURNAL_STATUS
                ),
                "business_write_policy": "forbid",
                "agent_artifact_sha256": self.manifest["artifacts"][
                    "host_agent_sha256"
                ],
                "host_agent_contract_sha256": self.manifest["artifacts"][
                    "host_agent_contract_sha256"
                ],
                "transport": self.manifest["topology"][role]["transport"],
                "observed_at": self.validated.role_observed_at[role],
                "host_identity_observed": True,
                "execution_supported": False,
                "production_contacted": False,
            }
            path = self.root / "normalized-roles" / f"{role}.json"
            role_digests[role] = private_json(path, document)
            role_paths[role] = path

        claim_paths: dict[str, Path] = {}
        claim_digests: dict[str, str] = {}
        for claim in MODULE.CLAIMS:
            document = {
                "schema": MODULE.CLAIM_SOURCE_SCHEMA,
                "campaign_id": self.manifest["campaign_id"],
                "operation_id": self.manifest["operation_id"],
                "release_sha": self.manifest["release_sha"],
                "manifest_sha256": self.manifest_sha256,
                "phase": MODULE.PHASE,
                "operation": MODULE.OPERATION,
                "claim": claim,
                "value": self.values[claim],
                "observed_at": self.now.isoformat(),
                "status": "observed",
            }
            path = self.root / "normalized-claims" / f"{claim}.json"
            claim_digests[claim] = private_json(path, document)
            claim_paths[claim] = path
        return role_paths, role_digests, claim_paths, claim_digests

    def rollback_document(self, role: str) -> dict[str, object]:
        artifacts = self.manifest["artifacts"]
        bot = role == "bot_fi"
        return {
            "schema": MODULE.ROLLBACK.ATTESTATION_SCHEMA,
            "status": "verified",
            "operation_id": self.manifest["operation_id"],
            "release_sha": self.manifest["release_sha"],
            "legacy_release_sha": self.manifest["legacy_release_sha"],
            "role": role,
            "rollback_closure_sha256": artifacts[
                "legacy_bot_rollback_sha256"
                if bot
                else "legacy_webapp_rollback_sha256"
            ],
            "legacy_redis_rollback_sha256": artifacts[
                "legacy_bot_redis_rollback_sha256"
                if bot
                else "legacy_webapp_redis_rollback_sha256"
            ],
            "sha256sums_sha256": "a" * 64,
            "backup_manifest_sha256": "b" * 64,
            "backup_artifact_set_sha256": "c" * 64,
            "backup_stamp": "20260727T171618Z",
            "database_restore_smoke_passed": True,
            "database_restore_smoke_table_count": 3,
            "sealed_file_count": (
                len(MODULE.ROLLBACK.ROLE_SEALED_FILES[role]) + 1
            ),
            "backup_artifact_count": len(MODULE.ROLLBACK.BACKUP_KINDS),
            "source_mutated": False,
            "production_contacted": True,
        }


class SecureInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_root_private_canonical_json_is_required(self):
        path = self.root / "input.json"
        document = {"alpha": 1, "beta": False}
        private_json(path, document)
        record = MODULE._read_private_json(path, label="test input")
        self.assertEqual(record.document, document)
        self.assertEqual(record.identity.mode, 0o600)

        path.write_text('{"beta": false, "alpha": 1}\n', encoding="ascii")
        path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "not canonical JSON",
        ):
            MODULE._read_private_json(path, label="test input")

    def test_duplicate_field_hardlink_and_missing_file_fail_closed(self):
        duplicate = self.root / "duplicate.json"
        private_bytes(duplicate, b'{"a":1,"a":2}')
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "duplicate",
        ):
            MODULE._read_private_json(duplicate, label="duplicate")

        target = self.root / "target.json"
        private_json(target, {"a": 1})
        os.link(target, self.root / "linked.json")
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "link count",
        ):
            MODULE._read_private_json(target, label="linked")

        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "unavailable",
        ):
            MODULE._read_private_json(
                self.root / "missing.json",
                label="missing",
            )

    def test_changed_file_identity_is_rejected(self):
        path = self.root / "stable.json"
        private_json(path, {"value": 1})
        record = MODULE._read_private_json(path, label="stable")
        replacement = self.root / "replacement.json"
        private_json(replacement, {"value": 1})
        os.replace(replacement, path)
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "identity or bytes changed",
        ):
            MODULE._assert_records_unchanged({"stable": record})

    def test_stale_and_future_observations_fail_closed(self):
        now = datetime.now(timezone.utc)
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "stale",
        ):
            MODULE._require_fresh(
                now - MODULE.SOURCE_MAX_AGE - timedelta(seconds=1),
                now=now,
                maximum=MODULE.SOURCE_MAX_AGE,
                label="observation",
            )
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "future",
        ):
            MODULE._require_fresh(
                now + MODULE.FUTURE_SKEW + timedelta(seconds=1),
                now=now,
                maximum=MODULE.SOURCE_MAX_AGE,
                label="observation",
            )

    def test_create_only_output_reuses_exact_and_rejects_difference(self):
        directory = self.root / "outputs"
        path, digest, status = MODULE._persist_document(
            directory,
            filename="result.json",
            document={"status": "one"},
        )
        self.assertEqual(status, "created")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            MODULE._persist_document(
                directory,
                filename="result.json",
                document={"status": "one"},
            )[1:],
            (digest, "reused"),
        )
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "already differs",
        ):
            MODULE._persist_document(
                directory,
                filename="result.json",
                document={"status": "two"},
            )


class EvidenceValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.fixture = PreFreezeFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _prepared_inventory_fixture(
        self,
    ) -> tuple[
        Path,
        Path,
        dict[str, str],
        dict[str, str],
        dict[str, MODULE.SecureRecord],
    ]:
        output_root = Path(
            self.fixture.manifest["deployment"][
                "controller_evidence_root"
            ]
        )
        private_directory(output_root)
        wa_manifest_path = (
            self.root / "wa-operation" / "operation-manifest.json"
        )
        private_json(wa_manifest_path, {"wa": "manifest"})
        wa_manifest = MODULE._read_private_json(
            wa_manifest_path,
            label="WA operation fixture",
        )
        contract_workers = {
            "bot_fi": "2" * 64,
            "webapp_fi": "2" * 64,
            "webapp_ir": "3" * 64,
        }
        role_manifest_sha256 = {
            "bot_fi": "5" * 64,
            "webapp_fi": "6" * 64,
            "webapp_ir": wa_manifest.sha256,
        }
        inputs = PREPARED.CollectionInputs(
            campaign_id=self.fixture.manifest["campaign_id"],
            operation_id=self.fixture.manifest["operation_id"],
            release_sha=self.fixture.manifest["release_sha"],
            release_tree_sha=self.fixture.manifest["release_tree_sha"],
            agent_sha256="1" * 64,
            roles={
                role: PREPARED.RoleBinding(
                    contract_worker_sha256=contract_workers[role],
                    role_manifest_sha256=role_manifest_sha256[role],
                )
                for role in PREPARED.ROLES
            },
        )
        issued_at = self.fixture.now - timedelta(seconds=8)
        requests = PREPARED._request_set(
            inputs,
            challenge="9" * 64,
            issued_at=issued_at,
            expires_at=issued_at
            + timedelta(seconds=PREPARED.REQUEST_LIFETIME_SECONDS),
        )
        responses: dict[str, dict] = {}
        command_times: dict[str, tuple[datetime, datetime]] = {}
        for index, role in enumerate(PREPARED.ROLES):
            started = issued_at + timedelta(seconds=index * 2 + 1)
            captured = started + timedelta(microseconds=500_000)
            completed = started + timedelta(seconds=1)
            responses[role] = PREPARED_FIXTURE._response(
                requests[role],
                captured_at=captured,
                marker=chr(ord("a") + index),
            )
            command_times[role] = (started, completed)
        aggregate = PREPARED.build_aggregate(
            inputs=inputs,
            requests=requests,
            responses=responses,
            command_times=command_times,
            now=self.fixture.now,
        )
        publication = PREPARED.publish_receipt_create_only(
            aggregate,
            requests=requests,
            responses=responses,
            output_root=output_root,
            now=self.fixture.now,
        )
        return (
            Path(publication["path"]),
            output_root,
            contract_workers,
            role_manifest_sha256,
            {"wa_ir_operation_manifest": wa_manifest},
        )

    def test_prepare_runtime_target_descriptor_must_match_v3_manifest(self):
        descriptor = copy.deepcopy(
            self.fixture.manifest["artifacts"]["convergence_runtime_targets"]
        )
        descriptor["sha256"] = "d" * 64
        metadata_path = self.root / "prepare-metadata.json"
        private_json(
            metadata_path,
            {
                "schema": MODULE.PREPARE.SET_SCHEMA,
                "capabilities": list(
                    MODULE.runtime_targets.RUNTIME_TARGET_CAPABILITIES
                ),
                "controller_bindings": {
                    "convergence_runtime_targets": descriptor,
                },
            },
        )
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "runtime target descriptor",
        ):
            MODULE._validate_prepare_metadata(
                self.fixture.context,
                metadata_path,
                self.root / "unused-compose.yml",
                {},
                {},
            )

        private_json(
            metadata_path,
            {
                "schema": MODULE.PREPARE.LEGACY_SET_SCHEMA,
            },
        )
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "fresh v3 prepare material",
        ):
            MODULE._validate_prepare_metadata(
                self.fixture.context,
                metadata_path,
                self.root / "unused-compose.yml",
                {},
                {},
            )

        private_json(
            metadata_path,
            {"schema": MODULE.PREPARE.SET_SCHEMA},
        )
        self.fixture.context.manifest["schema"] = "unsupported-cutover-schema"
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "fresh v4 cutover manifest",
        ):
            MODULE._validate_prepare_metadata(
                self.fixture.context,
                metadata_path,
                self.root / "unused-compose.yml",
                {},
                {},
            )

    def test_fresh_prepared_receipt_retains_exact_seven_sources(self):
        receipt, output_root, workers, manifests, records = (
            self._prepared_inventory_fixture()
        )
        observations = MODULE._validate_inventory_evidence(
            self.fixture.context,
            receipt_path=receipt,
            output_root=output_root,
            release_agent_sha256="1" * 64,
            release_contract_worker_sha256=workers,
            expected_role_manifest_sha256=manifests,
            now=self.fixture.now,
            records=records,
        )
        self.assertEqual(set(observations), set(MODULE.DOCKER_ROLES))
        inventory_labels = {
            "prepared_clone_inventory_receipt",
            *{
                f"{role}_inventory_{kind}"
                for role in MODULE.DOCKER_ROLES
                for kind in ("request", "response")
            },
        }
        self.assertEqual(
            inventory_labels,
            set(records) - {"wa_ir_operation_manifest"},
        )
        self.assertTrue(
            all(records[label].identity.mode == 0o600 for label in inventory_labels)
        )
        validated = MODULE.ValidatedInputs(
            **{
                **self.fixture.validated.__dict__,
                "records": records,
            }
        )
        closure = MODULE._input_closure_document(validated)
        self.assertTrue(
            closure["upstream_inventory_collection_performed"]
        )
        self.assertTrue(
            closure["upstream_inventory_production_contacted"]
        )
        self.assertFalse(closure["collection_performed"])
        self.assertFalse(closure["production_contacted"])
        self.assertIn(
            "prepared_clone_inventory_receipt",
            closure["source_files"],
        )

    def test_copied_fresh_receipt_and_root_are_not_manifest_authority(self):
        receipt, output_root, workers, manifests, records = (
            self._prepared_inventory_fixture()
        )
        copied_root = self.root / "copied-controller-evidence"
        shutil.copytree(output_root, copied_root)
        copied_receipt = copied_root / receipt.relative_to(output_root)
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "output root differs from the manifest",
        ):
            MODULE._validate_inventory_evidence(
                self.fixture.context,
                receipt_path=copied_receipt,
                output_root=copied_root,
                release_agent_sha256="1" * 64,
                release_contract_worker_sha256=workers,
                expected_role_manifest_sha256=manifests,
                now=self.fixture.now,
                records=dict(records),
            )

    def test_touch_cannot_refresh_expired_prepared_receipt(self):
        receipt, output_root, workers, manifests, records = (
            self._prepared_inventory_fixture()
        )
        future_ns = receipt.stat().st_mtime_ns + 10_000_000_000
        os.utime(
            receipt,
            ns=(receipt.stat().st_atime_ns, future_ns),
        )
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "fresh prepared-clone inventory receipt is invalid",
        ):
            MODULE._validate_inventory_evidence(
                self.fixture.context,
                receipt_path=receipt,
                output_root=output_root,
                release_agent_sha256="1" * 64,
                release_contract_worker_sha256=workers,
                expected_role_manifest_sha256=manifests,
                now=self.fixture.now + timedelta(seconds=90),
                records=dict(records),
            )

    def test_wrong_challenge_and_cross_role_substitution_fail(self):
        receipt, output_root, workers, manifests, records = (
            self._prepared_inventory_fixture()
        )
        displaced = receipt.parent.with_name("e" * 64)
        receipt.parent.rename(displaced)
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "fresh prepared-clone inventory receipt is invalid",
        ):
            MODULE._validate_inventory_evidence(
                self.fixture.context,
                receipt_path=displaced / receipt.name,
                output_root=output_root,
                release_agent_sha256="1" * 64,
                release_contract_worker_sha256=workers,
                expected_role_manifest_sha256=manifests,
                now=self.fixture.now,
                records=dict(records),
            )

        displaced.rename(receipt.parent)
        bot_request = (
            receipt.parent / PREPARED.REQUEST_FILENAMES["bot_fi"]
        ).read_bytes()
        substituted = (
            receipt.parent / PREPARED.REQUEST_FILENAMES["webapp_fi"]
        )
        substituted.write_bytes(bot_request)
        substituted.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "fresh prepared-clone inventory receipt is invalid",
        ):
            MODULE._validate_inventory_evidence(
                self.fixture.context,
                receipt_path=receipt,
                output_root=output_root,
                release_agent_sha256="1" * 64,
                release_contract_worker_sha256=workers,
                expected_role_manifest_sha256=manifests,
                now=self.fixture.now,
                records=dict(records),
            )

    def test_prepared_release_topology_and_role_manifest_bindings_are_exact(self):
        receipt, output_root, workers, manifests, records = (
            self._prepared_inventory_fixture()
        )
        wrong_workers = {**workers, "webapp_fi": "f" * 64}
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "release or topology binding differs",
        ):
            MODULE._validate_inventory_evidence(
                self.fixture.context,
                receipt_path=receipt,
                output_root=output_root,
                release_agent_sha256="1" * 64,
                release_contract_worker_sha256=wrong_workers,
                expected_role_manifest_sha256=manifests,
                now=self.fixture.now,
                records=dict(records),
            )

        substituted_records = dict(records)
        other_path = self.root / "wa-operation-other" / "manifest.json"
        private_json(other_path, {"wa": "other"})
        substituted_records["wa_ir_operation_manifest"] = (
            MODULE._read_private_json(
                other_path,
                label="substituted WA operation fixture",
            )
        )
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "role manifest differs",
        ):
            MODULE._validate_inventory_evidence(
                self.fixture.context,
                receipt_path=receipt,
                output_root=output_root,
                release_agent_sha256="1" * 64,
                release_contract_worker_sha256=workers,
                expected_role_manifest_sha256=manifests,
                now=self.fixture.now,
                records=substituted_records,
            )

        context = MODULE.CoordinatorContext(
            **{
                **self.fixture.context.__dict__,
                "manifest": copy.deepcopy(self.fixture.manifest),
            }
        )
        context.manifest["topology"]["webapp_fi"]["ssh_port"] = 22
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "release or topology binding differs",
        ):
            MODULE._validate_inventory_evidence(
                context,
                receipt_path=receipt,
                output_root=output_root,
                release_agent_sha256="1" * 64,
                release_contract_worker_sha256=workers,
                expected_role_manifest_sha256=manifests,
                now=self.fixture.now,
                records=dict(records),
            )

    def test_fi_and_wa_prepare_manifest_digest_substitution_fails(self):
        receipt, output_root, workers, manifests, records = (
            self._prepared_inventory_fixture()
        )
        for role, substituted_role in (
            ("bot_fi", "webapp_fi"),
            ("webapp_ir", "bot_fi"),
        ):
            with self.subTest(role=role), self.assertRaisesRegex(
                MODULE.PreFreezeEvidenceError,
                "release or topology binding differs",
            ):
                MODULE._validate_inventory_evidence(
                    self.fixture.context,
                    receipt_path=receipt,
                    output_root=output_root,
                    release_agent_sha256="1" * 64,
                    release_contract_worker_sha256=workers,
                    expected_role_manifest_sha256={
                        **manifests,
                        role: manifests[substituted_role],
                    },
                    now=self.fixture.now,
                    records=dict(records),
                )

    def test_release_closure_reads_exact_producer_archive_names(self):
        directory = private_directory(self.root / "release-closure")
        artifacts = self.fixture.manifest["artifacts"]
        bundle = b"release-bundle"
        private_bytes(directory / "release.bundle", bundle)
        artifacts["release_bundle_sha256"] = hashlib.sha256(bundle).hexdigest()
        artifacts["release_bundle_bytes"] = len(bundle)
        for kind in MODULE.IMAGE_KINDS:
            payload = f"{kind}-archive".encode("ascii")
            private_bytes(directory / f"{kind}-image.tar", payload)
            artifacts["image_artifacts"][kind][
                "archive_sha256"
            ] = hashlib.sha256(payload).hexdigest()
            artifacts["image_artifacts"][kind]["archive_bytes"] = len(payload)
        closure = {
            "release": {
                "bundle": {
                    "filename": "release.bundle",
                    "sha256": artifacts["release_bundle_sha256"],
                    "bytes": artifacts["release_bundle_bytes"],
                }
            },
            "images": copy.deepcopy(artifacts["image_artifacts"]),
        }
        closure_path = directory / "closure.json"
        private_json(closure_path, closure)
        closure_record = MODULE._read_private_json(
            closure_path,
            label="closure fixture",
        )
        records: dict[str, MODULE.SecureRecord] = {}
        with mock.patch.object(
            MODULE.FINLAND,
            "load_release_closure",
            return_value=(
                closure,
                closure_record.payload,
                closure_record.sha256,
            ),
        ):
            observed = MODULE._validate_release_closure(
                self.fixture.context,
                closure_path,
                records,
            )
        self.assertEqual(observed, closure)
        self.assertEqual(
            set(records),
            {
                "release_closure",
                "release_bundle",
                "app_image",
                "postgres_image",
                "redis_image",
                "nginx_image",
            },
        )

    def test_rollback_set_is_exact_and_rejects_role_substitution(self):
        paths: dict[str, Path] = {}
        for role in MODULE.ROLLBACK_ROLES:
            path = self.root / "rollback" / f"{role}.json"
            private_json(path, self.fixture.rollback_document(role))
            paths[role] = path
        records: dict[str, MODULE.SecureRecord] = {}
        result = MODULE._validate_rollback_evidence(
            self.fixture.context,
            paths,
            records,
        )
        self.assertEqual(set(result), set(MODULE.ROLLBACK_ROLES))

        substituted = self.fixture.rollback_document("bot_fi")
        private_json(
            self.root / "rollback-substitution" / "webapp.json",
            substituted,
        )
        bad_paths = {
            **paths,
            "webapp_fi": (
                self.root / "rollback-substitution" / "webapp.json"
            ),
        }
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "webapp_fi.*differs",
        ):
            MODULE._validate_rollback_evidence(
                self.fixture.context,
                bad_paths,
                {},
            )

    def test_rollback_extra_field_and_manifest_hash_tamper_fail(self):
        paths: dict[str, Path] = {}
        for role in MODULE.ROLLBACK_ROLES:
            document = self.fixture.rollback_document(role)
            if role == "bot_fi":
                document["unexpected"] = True
            path = self.root / "rollback-extra" / f"{role}.json"
            private_json(path, document)
            paths[role] = path
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "bot_fi.*differs",
        ):
            MODULE._validate_rollback_evidence(
                self.fixture.context,
                paths,
                {},
            )

        bot = self.fixture.rollback_document("bot_fi")
        bot["rollback_closure_sha256"] = "f" * 64
        private_json(self.root / "tamper" / "bot.json", bot)
        private_json(
            self.root / "tamper" / "webapp.json",
            self.fixture.rollback_document("webapp_fi"),
        )
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "bot_fi.*differs",
        ):
            MODULE._validate_rollback_evidence(
                self.fixture.context,
                {
                    "bot_fi": self.root / "tamper" / "bot.json",
                    "webapp_fi": self.root / "tamper" / "webapp.json",
                },
                {},
            )

    def test_claim_values_and_provenance_are_exact_and_manifest_bound(self):
        artifacts = self.fixture.manifest["artifacts"]
        closure = {
            "release": {
                "bundle": {"sha256": artifacts["release_bundle_sha256"]}
            },
            "images": artifacts["image_artifacts"],
        }
        rollback = {
            role: self.fixture.rollback_document(role)
            for role in MODULE.ROLLBACK_ROLES
        }
        nginx = {
            "global_generation_sha256": artifacts[
                "nginx_rollback_generation_sha256"
            ]
        }
        values = MODULE._claim_values(
            self.fixture.context,
            closure=closure,
            rollback=rollback,
            nginx_receipt=nginx,
        )
        self.assertEqual(set(values), set(MODULE.CLAIMS))
        self.assertEqual(len(values), 38)
        provenance = MODULE._claim_provenance()
        self.assertEqual(set(provenance), set(MODULE.CLAIMS))
        self.assertIn(
            "nginx_legacy_normal_receipt",
            provenance["active_route_generation_set_sha256"],
        )

        rollback["bot_fi"]["rollback_closure_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "differs from the manifest",
        ):
            MODULE._claim_values(
                self.fixture.context,
                closure=closure,
                rollback=rollback,
                nginx_receipt=nginx,
            )

    def _witness_documents(
        self,
        *,
        observed_epoch: int | None = None,
    ) -> tuple[dict, dict, dict, dict, dict]:
        manifest = self.fixture.manifest
        observed_epoch = observed_epoch or int(self.fixture.now.timestamp())
        release_manifest_sha256 = "1" * 64
        health = {
            "schema": MODULE.WITNESS.HEALTH_ATTESTATION_SCHEMA,
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "release_tree_sha": manifest["release_tree_sha"],
            "release_manifest_sha256": release_manifest_sha256,
            "observed_at_epoch": observed_epoch,
            "systemd": {"active_state": "active", "returncode": 0},
            "loopback_http": {
                name: {
                    "status_code": 200,
                    "content_type": "application/json",
                }
                for name in MODULE.WITNESS.HEALTH_EXPECTATIONS
            },
            "loopback_tls": {
                "host": MODULE.WITNESS.LOOPBACK_HOST,
                "port": MODULE.WITNESS.LOOPBACK_TLS_PORT,
                "server_name": manifest["topology"]["witness"]["host"],
                "certificate_encoding": "canonical-pem",
                "ca_sha256": "2" * 64,
                "server_cert_sha256": "3" * 64,
            },
        }
        health_sha256 = hashlib.sha256(
            canonical_json(health, newline=False)
        ).hexdigest()
        public = {
            "schema": MODULE.WITNESS.PUBLIC_INPUT_SCHEMA,
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "release_tree_sha": manifest["release_tree_sha"],
            "release_manifest_sha256": release_manifest_sha256,
            "health_attestation_sha256": health_sha256,
            "health_attested_at_epoch": observed_epoch,
            "ca_sha256": "2" * 64,
            "server_cert_sha256": "3" * 64,
            "native_release_reused": True,
            "current_mutated": False,
            "service_mutated": False,
            "legacy_secret_material_copied": False,
        }
        public_sha256 = hashlib.sha256(
            canonical_json(public, newline=False)
        ).hexdigest()
        stage = {
            "schema": MODULE.WITNESS.STAGE_OPERATION_SCHEMA,
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "release_tree_sha": manifest["release_tree_sha"],
            "candidate_release_root": os.fspath(
                MODULE.WITNESS.STAGED_RELEASE_PREFIX
                / manifest["release_sha"]
            ),
            "active_native_release_root": "/srv/writer-witness/current",
            "release_manifest_sha256": release_manifest_sha256,
            "release_subset_entries": 4,
            "health_attestation_sha256": health_sha256,
            "stage_attestation_sha256": public_sha256,
            "health_attested_at_epoch": observed_epoch,
            "native_release_reused": True,
            "current_mutated": False,
            "service_mutated": False,
            "legacy_secret_material_copied": False,
            "runtime_image_ids": {},
        }
        stage_sha256 = hashlib.sha256(
            canonical_json(stage, newline=False)
        ).hexdigest()
        binding = {
            "schema": MODULE.WITNESS.CONTROLLER_BINDING_SCHEMA,
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "role": "witness",
            "stage_operation_manifest_sha256": stage_sha256,
            "stage_attestation_sha256": public_sha256,
            "runtime_image_ids": {},
        }
        stage_bindings = {
            "roles": {
                "witness": {
                    "stage_operation_manifest_sha256": stage_sha256,
                    "stage_attestation_sha256": public_sha256,
                    "runtime_image_ids": {},
                }
            }
        }
        return health, public, stage, binding, stage_bindings

    def test_witness_closure_is_fresh_release_bound_and_role_exact(self):
        health, public, stage, binding, stage_bindings = (
            self._witness_documents()
        )
        paths = {
            "health": self.root / "witness" / "health.json",
            "public": self.root / "witness" / "public.json",
            "stage": self.root / "witness" / "stage.json",
            "binding": self.root / "witness" / "binding.json",
        }
        for name, document in (
            ("health", health),
            ("public", public),
            ("stage", stage),
            ("binding", binding),
        ):
            private_json(paths[name], document, newline=False)
        records: dict[str, MODULE.SecureRecord] = {}
        observed = MODULE._validate_witness_evidence(
            self.fixture.context,
            health_path=paths["health"],
            public_path=paths["public"],
            stage_path=paths["stage"],
            binding_path=paths["binding"],
            stage_bindings=stage_bindings,
            now=self.fixture.now,
            records=records,
        )
        self.assertEqual(observed, self.fixture.now)
        self.assertEqual(
            set(records),
            {
                "witness_health",
                "witness_public_input",
                "witness_stage_operation",
                "witness_stage_binding",
            },
        )

        binding["role"] = "webapp_ir"
        private_json(
            self.root / "witness-substitution" / "binding.json",
            binding,
            newline=False,
        )
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "substituted",
        ):
            MODULE._validate_witness_evidence(
                self.fixture.context,
                health_path=paths["health"],
                public_path=paths["public"],
                stage_path=paths["stage"],
                binding_path=(
                    self.root / "witness-substitution" / "binding.json"
                ),
                stage_bindings=stage_bindings,
                now=self.fixture.now,
                records={},
            )

    def test_stale_witness_health_is_rejected(self):
        old = int(
            (
                self.fixture.now
                - timedelta(
                    seconds=MODULE.WITNESS.MAX_HEALTH_AGE_SECONDS + 1
                )
            ).timestamp()
        )
        health, public, stage, binding, stage_bindings = (
            self._witness_documents(observed_epoch=old)
        )
        paths = {}
        for name, document in (
            ("health", health),
            ("public", public),
            ("stage", stage),
            ("binding", binding),
        ):
            path = self.root / "stale-witness" / f"{name}.json"
            private_json(path, document, newline=False)
            paths[name] = path
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "stale",
        ):
            MODULE._validate_witness_evidence(
                self.fixture.context,
                health_path=paths["health"],
                public_path=paths["public"],
                stage_path=paths["stage"],
                binding_path=paths["binding"],
                stage_bindings=stage_bindings,
                now=self.fixture.now,
                records={},
            )

    def _nginx_fixture(
        self,
        *,
        missing_vhost: bool = False,
    ) -> tuple[Path, Path, dict]:
        manifest = self.fixture.manifest
        artifacts = manifest["artifacts"]
        root = private_directory(self.root / "nginx")
        roles = {}
        for index, role in enumerate(MODULE.NGINX.ROLE_ORDER, start=1):
            role_root = private_directory(root / role)
            manifest_document = {"role": role}
            manifest_payload = canonical_json(manifest_document)
            archive_payload = f"{role}-archive".encode("ascii")
            manifest_sha256 = private_json(
                role_root / "nginx-generations-manifest.json",
                manifest_document,
            )
            archive_sha256 = private_bytes(
                role_root / "nginx-generations.tar",
                archive_payload,
            )
            roles[role] = {
                "expected_host": manifest["topology"][role]["host"],
                "manifest_sha256": manifest_sha256,
                "manifest_bytes": len(manifest_payload),
                "archive_sha256": archive_sha256,
                "archive_bytes": len(archive_payload),
                "generation_sha256": {
                    "legacy-normal": artifacts[
                        "nginx_rollback_generation_sha256"
                    ]
                },
                "legacy_upstream_closure_sha256": str(index) * 64,
            }
        aggregate = {
            "schema": MODULE.NGINX.GENERATION.PRODUCER_SCHEMA,
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "release_tree_sha": manifest["release_tree_sha"],
            "shadow_release_root": os.fspath(
                Path(manifest["deployment"]["shadow_root"])
                / "releases"
                / manifest["release_sha"]
            ),
            "roles": roles,
            "generation_sha256": {
                "legacy-normal": artifacts[
                    "nginx_rollback_generation_sha256"
                ]
            },
            "nginx_legacy_normal_generation_sha256": artifacts[
                "nginx_rollback_generation_sha256"
            ],
            "nginx_rollback_generation_sha256": artifacts[
                "nginx_rollback_generation_sha256"
            ],
            "nginx_freeze_generation_sha256": artifacts[
                "nginx_freeze_generation_sha256"
            ],
            "nginx_shadow_readonly_generation_sha256": artifacts[
                "nginx_shadow_readonly_generation_sha256"
            ],
            "nginx_shadow_writable_generation_sha256": artifacts[
                "nginx_shadow_writable_generation_sha256"
            ],
            "legacy_upstream_closure_sha256": "f" * 64,
            "contains_tls_key_or_certificate_body": False,
            "active_configuration_mutated": False,
            "production_contacted": False,
        }
        aggregate_path = root / "aggregate.json"
        aggregate_sha256 = private_json(aggregate_path, aggregate)
        vhosts = {
            vhost: {"get": 200}
            for names in MODULE.CONTROLLER.PRODUCTION_VHOSTS.values()
            for vhost in names
        }
        if missing_vhost:
            vhosts.pop(next(iter(vhosts)))
        receipt = {
            "role_bindings": {
                role: {
                    "manifest_sha256": row["manifest_sha256"],
                    "archive_sha256": row["archive_sha256"],
                }
                for role, row in roles.items()
            },
            "global_generation_sha256": artifacts[
                "nginx_rollback_generation_sha256"
            ],
            "captured_at_epoch": int(self.fixture.now.timestamp()),
            "external_readback": {
                "states": ["legacy-normal"],
                "vhosts": vhosts,
            },
        }
        receipt_path = root / "receipt.json"
        receipt_sha256 = private_json(receipt_path, receipt)
        self.nginx_mock_hashes = {
            field: artifacts[field]
            for field in (
                "nginx_rollback_generation_sha256",
                "nginx_freeze_generation_sha256",
                "nginx_shadow_readonly_generation_sha256",
                "nginx_shadow_writable_generation_sha256",
            )
        }
        self.nginx_receipt_result = (
            receipt,
            receipt_sha256,
            aggregate_sha256,
        )
        return aggregate_path, receipt_path, receipt

    def test_active_legacy_normal_external_readback_is_required(self):
        aggregate_path, receipt_path, receipt = self._nginx_fixture()
        with (
            mock.patch.object(
                MODULE.INVENTORY,
                "PRE_FREEZE_CURRENT_OPERATION_RECEIPT_SCHEMA",
                "test-prepared-inventory-v1",
                create=True,
            ),
            mock.patch.object(
                MODULE.NGINX,
                "PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA",
                "test-fresh-nginx-v1",
                create=True,
            ),
            mock.patch.object(
                MODULE.TEMPLATE,
                "_verify_nginx_material",
                return_value=self.nginx_mock_hashes,
            ),
            mock.patch.object(
                MODULE.NGINX,
                "load_state_receipt",
                return_value=(
                    receipt,
                    self.nginx_receipt_result[1],
                ),
            ),
        ):
            observed, _timestamp = MODULE._validate_nginx_evidence(
                self.fixture.context,
                aggregate_path=aggregate_path,
                receipt_path=receipt_path,
                now=self.fixture.now,
                records={},
            )
        self.assertEqual(
            observed["external_readback"]["states"],
            ["legacy-normal"],
        )

        aggregate_path, receipt_path, receipt = self._nginx_fixture(
            missing_vhost=True
        )
        with (
            mock.patch.object(
                MODULE.INVENTORY,
                "PRE_FREEZE_CURRENT_OPERATION_RECEIPT_SCHEMA",
                "test-prepared-inventory-v1",
                create=True,
            ),
            mock.patch.object(
                MODULE.NGINX,
                "PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA",
                "test-fresh-nginx-v1",
                create=True,
            ),
            mock.patch.object(
                MODULE.TEMPLATE,
                "_verify_nginx_material",
                return_value=self.nginx_mock_hashes,
            ),
            mock.patch.object(
                MODULE.NGINX,
                "load_state_receipt",
                return_value=(
                    receipt,
                    self.nginx_receipt_result[1],
                ),
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.PreFreezeEvidenceError,
                "external route readback is absent",
            ):
                MODULE._validate_nginx_evidence(
                    self.fixture.context,
                    aggregate_path=aggregate_path,
                    receipt_path=receipt_path,
                    now=self.fixture.now,
                    records={},
                )

    def test_normalized_four_role_evidence_passes_immutable_verifier(self):
        role_paths, role_digests, claim_paths, claim_digests = (
            self.fixture.normalized_inputs()
        )
        document, path, digest = MODULE._build_phase_evidence(
            self.fixture.validated,
            captured_at=self.fixture.now.isoformat(),
            role_paths=role_paths,
            role_digests=role_digests,
            claim_paths=claim_paths,
            claim_digests=claim_digests,
            now=self.fixture.now,
        )
        self.assertEqual(
            [row["role"] for row in document["role_attestations"]],
            list(MODULE.ROLES),
        )
        self.assertEqual(set(document["claims"]), set(MODULE.CLAIMS))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            digest,
        )

    def test_normalized_role_substitution_and_extra_field_fail(self):
        role_paths, role_digests, claim_paths, claim_digests = (
            self.fixture.normalized_inputs()
        )
        target = role_paths["witness"]
        document = json.loads(target.read_text(encoding="ascii"))
        document["role"] = "webapp_ir"
        document["unexpected"] = True
        role_digests["witness"] = private_json(target, document)
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "normalized verifier inputs failed",
        ):
            MODULE._build_phase_evidence(
                self.fixture.validated,
                captured_at=self.fixture.now.isoformat(),
                role_paths=role_paths,
                role_digests=role_digests,
                claim_paths=claim_paths,
                claim_digests=claim_digests,
                now=self.fixture.now,
            )


class ControllerBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.fixture = PreFreezeFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_transition_arguments_are_exact_and_reject_prior_or_duplicates(self):
        begin = MODULE.ControllerTransition(
            action="begin-phase",
            phase=MODULE.PHASE,
            manifest_path=Path("/root/manifest.json"),
            approval_path=Path("/root/approval.json"),
            approval_policy_path=Path("/root/policy.json"),
        )
        argv = MODULE._transition_arguments(begin)
        self.assertNotIn("--evidence", argv)
        self.assertEqual(argv[-3:], ["--apply", "--confirm", MODULE.CONTROLLER.APPLY_CONFIRMATION])

        complete = MODULE.ControllerTransition(
            action="complete-phase",
            phase=MODULE.PHASE,
            manifest_path=Path("/root/manifest.json"),
            approval_path=Path("/root/approval.json"),
            approval_policy_path=Path("/root/policy.json"),
            evidence_path=Path("/root/evidence.json"),
            role_validation=tuple(
                f"{role}=/root/{role}.json" for role in MODULE.ROLES
            ),
            claim_source=tuple(
                f"{claim}=/root/{claim}.json" for claim in MODULE.CLAIMS
            ),
        )
        argv = MODULE._transition_arguments(complete)
        self.assertEqual(argv.count("--role-validation"), 4)
        self.assertEqual(argv.count("--claim-source"), 38)

        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "not exact",
        ):
            MODULE._transition_arguments(
                MODULE.ControllerTransition(
                    **{
                        **complete.__dict__,
                        "prior_phase_evidence": ("prior=/root/prior.json",),
                    }
                )
            )
        duplicate_roles = (
            *complete.role_validation[:-1],
            complete.role_validation[0],
        )
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "invalid",
        ):
            MODULE._transition_arguments(
                MODULE.ControllerTransition(
                    **{
                        **complete.__dict__,
                        "role_validation": duplicate_roles,
                    }
                )
            )

    def test_public_controller_baseexception_fails_closed(self):
        transition = MODULE.ControllerTransition(
            action="begin-phase",
            phase=MODULE.PHASE,
            manifest_path=Path("/root/manifest.json"),
            approval_path=Path("/root/approval.json"),
            approval_policy_path=Path("/root/policy.json"),
        )
        with mock.patch.object(
            MODULE.INVENTORY,
            "_bounded_command",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaisesRegex(
                MODULE.PreFreezeEvidenceError,
                "interrupted or failed closed",
            ):
                MODULE.invoke_public_controller(transition)

    def test_callback_eof_fails_closed(self):
        with mock.patch.object(
            MODULE,
            "_verify_runtime_authorization",
        ):
            with self.assertRaisesRegex(
                MODULE.PreFreezeEvidenceError,
                "transition failed closed",
            ):
                MODULE._invoke_controller_transition(
                    self.fixture.context,
                    callback=mock.Mock(side_effect=EOFError),
                    action="begin-phase",
                    authority_check=lambda _checkpoint: None,
                )

    def test_completed_phase_is_idempotent_but_digest_bound(self):
        digest = "d" * 64
        journal = {
            "completed_phases": [MODULE.PHASE],
            "phase_evidence_sha256": {MODULE.PHASE: digest},
        }
        callback = mock.Mock()
        with (
            mock.patch.object(MODULE, "_read_journal", return_value=journal),
            mock.patch.object(
                MODULE,
                "_validate_verification_receipt",
                return_value=(Path("/root/receipt.json"), "e" * 64),
            ),
        ):
            result = MODULE._complete_phase(
                self.fixture.context,
                callback=callback,
                evidence_path=Path("/root/evidence.json"),
                evidence_sha256=digest,
                role_validation={},
                claim_source={},
                authority_check=lambda _checkpoint: None,
            )
            self.assertEqual(result[0], journal)
            callback.assert_not_called()
            with self.assertRaisesRegex(
                MODULE.PreFreezeEvidenceError,
                "differs",
            ):
                MODULE._complete_phase(
                    self.fixture.context,
                    callback=callback,
                    evidence_path=Path("/root/evidence.json"),
                    evidence_sha256="f" * 64,
                    role_validation={},
                    claim_source={},
                    authority_check=lambda _checkpoint: None,
                )

    def test_apply_requires_anonymous_read_pipe(self):
        with self.assertRaisesRegex(
            MODULE.PreFreezeEvidenceError,
            "anonymous controller-liveness pipe",
        ), mock.patch.object(MODULE, "_load_context") as load:
            MODULE.execute_pre_freeze_evidence(
                manifest_path=Path("/root/manifest.json"),
                approval_path=Path("/root/approval.json"),
                approval_policy_path=Path("/root/policy.json"),
                evidence_paths=mock.Mock(),
                confirm="confirmation",
            )
        load.assert_not_called()

        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaisesRegex(
                MODULE.PreFreezeEvidenceError,
                "not an anonymous read pipe",
            ):
                MODULE.ControllerLiveness(write_fd)
            with self.assertRaisesRegex(
                MODULE.PreFreezeEvidenceError,
                "retains a liveness pipe writer",
            ):
                MODULE.ControllerLiveness(read_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_eof_first_and_forbidden_data_fail_before_apply(self):
        with ExternalLivenessPipe() as liveness:
            liveness.close_writer()
            with self.assertRaisesRegex(
                MODULE.LiveControllerAuthorityLost,
                "EOF",
            ):
                with MODULE._signal_cancellation_guard():
                    with MODULE.ControllerLiveness(liveness.read_fd):
                        self.fail("EOF-first pipe entered apply body")

        with ExternalLivenessPipe() as liveness:
            liveness.close_writer(with_data=True)
            with self.assertRaisesRegex(
                MODULE.LiveControllerAuthorityLost,
                "forbidden data",
            ):
                with MODULE._signal_cancellation_guard():
                    with MODULE.ControllerLiveness(liveness.read_fd):
                        self.fail("data-bearing pipe entered apply body")

    def test_live_eof_interrupts_inflight_apply_and_cleans_watcher(self):
        started = time.monotonic()
        with ExternalLivenessPipe() as liveness:
            with self.assertRaisesRegex(
                MODULE.LiveControllerAuthorityLost,
                "EOF|SIGUSR1",
            ):
                with MODULE._signal_cancellation_guard():
                    with MODULE.ControllerLiveness(
                        liveness.read_fd
                    ) as watcher:
                        liveness.close_writer()
                        while time.monotonic() - started < 2:
                            time.sleep(0.02)
                            watcher.check()
                        self.fail("controller EOF did not interrupt apply")
        self.assertFalse(
            any(
                thread.name == "pre-freeze-controller-liveness"
                and thread.is_alive()
                for thread in __import__("threading").enumerate()
            )
        )

    def test_hup_int_term_handler_is_one_shot_and_restored(self):
        previous = {
            signum: signal.getsignal(signum)
            for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
        }
        with MODULE._signal_cancellation_guard():
            handler = signal.getsignal(signal.SIGTERM)
            self.assertTrue(callable(handler))
            with self.assertRaisesRegex(
                MODULE.LiveControllerAuthorityLost,
                "SIGTERM",
            ):
                handler(signal.SIGTERM, None)
            self.assertTrue(MODULE._SIGNAL_SEEN)
            self.assertIsNone(handler(signal.SIGINT, None))
        for signum, expected in previous.items():
            self.assertIs(signal.getsignal(signum), expected)

    def test_partial_signal_install_failure_restores_in_reverse(self):
        handled = (
            signal.SIGHUP,
            signal.SIGINT,
            signal.SIGTERM,
            signal.SIGUSR1,
        )
        before = {
            signum: signal.getsignal(signum) for signum in handled
        }
        real_signal = signal.signal
        failed = False

        def fail_once(signum, handler):  # noqa: ANN001, ANN202
            nonlocal failed
            if (
                signum == signal.SIGTERM
                and callable(handler)
                and not failed
            ):
                failed = True
                raise OSError("synthetic install failure")
            return real_signal(signum, handler)

        with mock.patch.object(
            MODULE.signal,
            "signal",
            side_effect=fail_once,
        ):
            with self.assertRaisesRegex(
                OSError,
                "synthetic install failure",
            ):
                with MODULE._signal_cancellation_guard():
                    self.fail("guard body must not run")
        self.assertEqual(
            {
                signum: signal.getsignal(signum)
                for signum in handled
            },
            before,
        )

    def test_signal_inside_begin_is_deferred_until_journal_readback(self):
        initial = {
            "completed_phases": [],
            "status": "active",
            "started_phase": None,
        }
        started = {
            "completed_phases": [],
            "status": "phase_started",
            "started_phase": MODULE.PHASE,
        }
        callback_called = False

        def callback(transition):  # noqa: ANN001, ANN202
            nonlocal callback_called
            callback_called = True
            handler = signal.getsignal(signal.SIGTERM)
            self.assertTrue(callable(handler))
            handler(signal.SIGTERM, None)
            return {
                "status": "phase-started",
                "action": transition.action,
                "journal": started,
                "production_contacted": False,
            }

        readback = mock.Mock(side_effect=[initial, started])
        with (
            mock.patch.object(MODULE, "_read_journal", readback),
            mock.patch.object(MODULE, "_verify_runtime_authorization"),
        ):
            with self.assertRaisesRegex(
                MODULE.LiveControllerAuthorityLost,
                "SIGTERM",
            ):
                with MODULE._signal_cancellation_guard():
                    MODULE._begin_phase(
                        self.fixture.context,
                        callback=callback,
                        authority_check=lambda _checkpoint: None,
                    )
        self.assertTrue(callback_called)
        self.assertEqual(readback.call_count, 2)

    def test_signal_inside_complete_defers_through_receipt_readback(self):
        evidence_sha256 = "d" * 64
        initial = {
            "completed_phases": [],
            "status": "phase_started",
            "started_phase": MODULE.PHASE,
        }
        completed = {
            "completed_phases": [MODULE.PHASE],
            "status": "active",
            "started_phase": None,
            "phase_evidence_sha256": {
                MODULE.PHASE: evidence_sha256
            },
            "phase_verification_sha256": {
                MODULE.PHASE: "e" * 64
            },
        }

        def callback(transition):  # noqa: ANN001, ANN202
            handler = signal.getsignal(signal.SIGINT)
            self.assertTrue(callable(handler))
            handler(signal.SIGINT, None)
            return {
                "status": "phase-complete",
                "action": transition.action,
                "journal": completed,
                "production_contacted": False,
            }

        receipt = mock.Mock(
            return_value=(Path("/root/receipt.json"), "e" * 64)
        )
        readback = mock.Mock(side_effect=[initial, completed])
        with (
            mock.patch.object(MODULE, "_read_journal", readback),
            mock.patch.object(MODULE, "_verify_runtime_authorization"),
            mock.patch.object(
                MODULE,
                "_validate_verification_receipt",
                receipt,
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.LiveControllerAuthorityLost,
                "SIGINT",
            ):
                with MODULE._signal_cancellation_guard():
                    MODULE._complete_phase(
                        self.fixture.context,
                        callback=callback,
                        evidence_path=Path("/root/evidence.json"),
                        evidence_sha256=evidence_sha256,
                        role_validation={
                            role: Path(f"/root/{role}.json")
                            for role in MODULE.ROLES
                        },
                        claim_source={
                            claim: Path(f"/root/{claim}.json")
                            for claim in MODULE.CLAIMS
                        },
                        authority_check=lambda _checkpoint: None,
                    )
        self.assertEqual(readback.call_count, 2)
        receipt.assert_called_once()

    def test_public_execute_composes_nonoptional_liveness_checks(self):
        supplemental: list[str] = []

        def fake_execute(**kwargs):  # noqa: ANN003, ANN202
            checker = kwargs["authority_check"]
            checker("checkpoint-one")
            checker("checkpoint-two")
            return {"status": "complete"}

        with ExternalLivenessPipe() as liveness:
            with mock.patch.object(
                MODULE,
                "_execute_pre_freeze_evidence_with_authority",
                side_effect=fake_execute,
            ):
                result = MODULE.execute_pre_freeze_evidence(
                    manifest_path=Path("/root/manifest.json"),
                    approval_path=Path("/root/approval.json"),
                    approval_policy_path=Path("/root/policy.json"),
                    evidence_paths=mock.Mock(),
                    confirm="confirmation",
                    controller_liveness_fd=liveness.read_fd,
                    authority_check=supplemental.append,
                )
            self.assertEqual(result, {"status": "complete"})
            self.assertEqual(
                supplemental,
                ["checkpoint-one", "checkpoint-two"],
            )

    def test_expired_approval_blocks_before_output_or_begin(self):
        plan = {"required_confirmation": "confirmed"}
        ensure = mock.Mock()
        begin = mock.Mock()
        with ExternalLivenessPipe() as liveness:
            with (
                mock.patch.object(
                    MODULE,
                    "_load_context",
                    return_value=(self.fixture.context, {}),
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_inputs",
                    return_value=self.fixture.validated,
                ),
                mock.patch.object(
                    MODULE,
                    "_coordinator_plan",
                    return_value=plan,
                ),
                mock.patch.object(
                    MODULE,
                    "_verify_runtime_authorization",
                    side_effect=MODULE.PreFreezeEvidenceError(
                        "production cutover authorization is invalid or expired"
                    ),
                ),
                mock.patch.object(
                    MODULE,
                    "_ensure_private_directory",
                    ensure,
                ),
                mock.patch.object(MODULE, "_begin_phase", begin),
            ):
                with self.assertRaisesRegex(
                    MODULE.PreFreezeEvidenceError,
                    "expired",
                ):
                    MODULE.execute_pre_freeze_evidence(
                        manifest_path=Path("/root/manifest.json"),
                        approval_path=Path("/root/approval.json"),
                        approval_policy_path=Path("/root/policy.json"),
                        evidence_paths=mock.Mock(),
                        confirm="confirmed",
                        controller_liveness_fd=liveness.read_fd,
                        now=self.fixture.now,
                    )
            ensure.assert_not_called()
            begin.assert_not_called()
            self.assertFalse(self.fixture.context.output_root.exists())

    def test_apply_failure_reports_unknown_journal_and_reconciliation(self):
        namespace = mock.Mock(
            apply=True,
            confirm="confirmed",
            controller_liveness_fd=7,
            manifest=Path("/root/manifest.json"),
            approval=Path("/root/approval.json"),
            approval_policy=Path("/root/policy.json"),
        )
        parser = mock.Mock()
        parser.parse_args.return_value = namespace
        output = io.StringIO()
        with (
            mock.patch.object(MODULE, "_parser", return_value=parser),
            mock.patch.object(MODULE, "_evidence_paths", return_value=mock.Mock()),
            mock.patch.object(
                MODULE,
                "execute_pre_freeze_evidence",
                side_effect=MODULE.PreFreezeEvidenceError("failed after begin"),
            ),
            redirect_stdout(output),
        ):
            status = MODULE.main([])
        result = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertIsNone(result["journal_mutated"])
        self.assertTrue(result["reconciliation_required"])
        self.assertFalse(result["production_contacted"])

    def test_success_reports_truthful_per_invocation_journal_mutation(self):
        input_sha256 = "1" * 64
        evidence_sha256 = "2" * 64
        derivation_sha256 = "3" * 64
        receipt_sha256 = "4" * 64
        input_path = self.root / "input.json"
        evidence_path = self.root / "evidence.json"
        derivation_path = self.root / "derivation.json"
        receipt_path = self.root / "receipt.json"
        final_journal = {
            "phase_evidence_sha256": {
                MODULE.PHASE: evidence_sha256
            },
            "phase_verification_sha256": {
                MODULE.PHASE: receipt_sha256
            },
        }
        for already_complete, expected_mutated in (
            (True, False),
            (False, True),
        ):
            context = MODULE.CoordinatorContext(
                **{
                    **self.fixture.context.__dict__,
                    "journal": {
                        "completed_phases": (
                            [MODULE.PHASE] if already_complete else []
                        )
                    },
                }
            )
            validated = MODULE.ValidatedInputs(
                **{
                    **self.fixture.validated.__dict__,
                    "context": context,
                }
            )
            with self.subTest(already_complete=already_complete), (
                mock.patch.object(
                    MODULE,
                    "_load_context",
                    return_value=(context, {}),
                )
            ), mock.patch.object(
                MODULE,
                "_validate_inputs",
                return_value=validated,
            ), mock.patch.object(
                MODULE,
                "_coordinator_plan",
                return_value={
                    "required_confirmation": "confirmed",
                    "input_closure_sha256": input_sha256,
                },
            ), mock.patch.object(
                MODULE,
                "_verify_runtime_authorization",
            ), mock.patch.object(
                MODULE,
                "_ensure_private_directory",
            ), mock.patch.object(
                MODULE,
                "_write_input_closure",
                return_value=(
                    {"captured_at": self.fixture.now.isoformat()},
                    input_path,
                    input_sha256,
                ),
            ), mock.patch.object(
                MODULE,
                "_assert_records_unchanged",
            ), mock.patch.object(
                MODULE,
                "_begin_phase",
            ), mock.patch.object(
                MODULE,
                "_write_role_validations",
                return_value=({}, {}),
            ), mock.patch.object(
                MODULE,
                "_write_claim_sources",
                return_value=({}, {}),
            ), mock.patch.object(
                MODULE,
                "_build_phase_evidence",
                return_value=({}, evidence_path, evidence_sha256),
            ), mock.patch.object(
                MODULE,
                "_write_derivation",
                return_value=(derivation_path, derivation_sha256),
            ), mock.patch.object(
                MODULE,
                "_complete_phase",
                return_value=(
                    final_journal,
                    receipt_path,
                    receipt_sha256,
                ),
            ), mock.patch.object(
                MODULE,
                "_read_private_json",
                side_effect=[
                    mock.Mock(sha256=derivation_sha256),
                    mock.Mock(sha256=evidence_sha256),
                ],
            ):
                result = MODULE._execute_pre_freeze_evidence_with_authority(
                    manifest_path=Path("/root/manifest.json"),
                    approval_path=Path("/root/approval.json"),
                    approval_policy_path=Path("/root/policy.json"),
                    evidence_paths=mock.Mock(),
                    confirm="confirmed",
                    authority_check=lambda _checkpoint: None,
                    now=self.fixture.now,
                )
            self.assertIs(
                result["journal_mutated"],
                expected_mutated,
            )
            self.assertEqual(
                result["status"],
                "already-complete" if already_complete else "complete",
            )

    def test_plan_declares_no_collection_and_required_liveness(self):
        plan = MODULE._coordinator_plan(self.fixture.validated)
        self.assertTrue(plan["fresh_prepared_inventory_receipt_required"])
        self.assertTrue(plan["upstream_inventory_collection_performed"])
        self.assertTrue(plan["upstream_inventory_production_contacted"])
        self.assertFalse(plan["collection_performed"])
        self.assertFalse(plan["host_contact_performed"])
        self.assertFalse(plan["docker_contact_performed"])
        self.assertFalse(plan["object_storage_contact_performed"])
        self.assertFalse(plan["caller_truth_values_accepted"])
        self.assertTrue(plan["controller_liveness_pipe_required"])
        self.assertIn(
            plan["input_closure_sha256"],
            plan["required_confirmation"],
        )

    def test_cli_requires_receipt_pair_and_exposes_no_legacy_pair_flags(self):
        options = MODULE._parser()._option_string_actions
        self.assertIn("--inventory-receipt", options)
        self.assertIn("--inventory-output-root", options)
        self.assertNotIn("--inventory-request", options)
        self.assertNotIn("--inventory-response", options)

    def test_touching_legacy_collections_cannot_unlock_public_plan(self):
        legacy = self.root / "legacy-inventory-response.json"
        private_json(legacy, {"status": "captured-stable"})
        release_validation = mock.Mock()
        paths = mock.Mock()
        with (
            mock.patch.object(
                MODULE.INVENTORY,
                "PRE_FREEZE_CURRENT_OPERATION_RECEIPT_SCHEMA",
                None,
                create=True,
            ),
            mock.patch.object(
                MODULE.NGINX,
                "PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA",
                None,
                create=True,
            ),
            mock.patch.object(
                MODULE,
                "_load_context",
                return_value=(self.fixture.context, {}),
            ),
            mock.patch.object(
                MODULE,
                "_validate_release_files",
                release_validation,
            ),
        ):
            for timestamp in (
                time.time(),
                time.time() + 3600,
            ):
                os.utime(legacy, (timestamp, timestamp))
                with self.assertRaisesRegex(
                    MODULE.PreFreezeEvidenceError,
                    "legacy file mtime.*rejected",
                ):
                    MODULE.plan_pre_freeze_evidence(
                        manifest_path=Path("/root/manifest.json"),
                        approval_path=Path("/root/approval.json"),
                        approval_policy_path=Path("/root/policy.json"),
                        evidence_paths=paths,
                        now=self.fixture.now,
                    )
        release_validation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
