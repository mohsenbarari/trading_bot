from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import tempfile
import unittest
from unittest import mock

from scripts import (
    produce_production_shadow_frozen_final_restore_phase_evidence as MODULE,
)
from scripts import production_shadow_cutover_controller as CONTROLLER
from scripts import verify_production_shadow_phase_evidence as VERIFY
from tests import (
    test_orchestrate_production_shadow_frozen_final_restore as RESTORE_TESTS,
)
from tests.test_production_shadow_cutover_controller import (
    manifest_payload,
    write_controller_manifest,
)


NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)
POSTGRES_SNAPSHOT_SET_SHA256 = "1" * 64
FILE_SNAPSHOT_SET_SHA256 = "2" * 64
RESTORE_RESULT_SET_SHA256 = "3" * 64
INVENTORY_CLOSURE_SHA256 = "4" * 64


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def digest_for(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def rule_value(
    phase: str,
    claim: str,
    rule: VERIFY.ClaimRule,
    manifest: dict,
) -> object:
    if rule.kind == "exact":
        return rule.expected
    if phase == "final_snapshot_hashes":
        if claim == "postgres_snapshot_set_sha256":
            return POSTGRES_SNAPSHOT_SET_SHA256
        if claim == "reviewed_file_snapshot_set_sha256":
            return FILE_SNAPSHOT_SET_SHA256
    binding = VERIFY.PHASE_MANIFEST_CLAIM_BINDINGS.get(phase, {}).get(
        claim
    )
    if binding is not None:
        return VERIFY._manifest_artifact_binding_value(  # noqa: SLF001
            manifest["artifacts"],
            binding,
        )
    if rule.kind == "nonzero-sha256":
        return digest_for(f"{phase}:{claim}")
    if rule.kind == "positive-int":
        return 1
    if rule.kind == "immutable-image-id":
        return "sha256:" + digest_for(f"{phase}:{claim}")
    if rule.kind == "nonempty-string":
        return f"{phase}:{claim}"
    raise AssertionError(rule.kind)


class PhaseEvidenceFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.output = root / "output"
        self.output.mkdir(mode=0o700)
        self.manifest_path = root / "cutover-manifest.json"
        self.manifest = manifest_payload()
        self.manifest["created_at"] = NOW.isoformat()
        self.manifest["artifacts"]["phase_evidence_schema_sha256"] = (
            VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
        )
        write_controller_manifest(self.manifest_path, self.manifest)
        (
            self.validated_manifest,
            self.manifest_sha256,
        ) = CONTROLLER.read_root_only_manifest(self.manifest_path)
        self.plan = CONTROLLER.render_plan(
            self.validated_manifest,
            manifest_sha256=self.manifest_sha256,
            manifest_path=self.manifest_path,
        )
        self.prior_paths: dict[str, Path] = {}
        self.prior_digests: dict[str, str] = {}
        self._write_prior_evidence()
        self._start_cutover_journal()
        self.role_paths: dict[str, Path] = {}
        self.claim_paths: dict[str, Path] = {}
        self._write_role_validations()
        self._write_claim_sources()

    def _write_prior_evidence(self) -> None:
        prior_root = self.root / "prior"
        prior_root.mkdir(mode=0o700)
        for phase in CONTROLLER.PHASES[
            : CONTROLLER.PHASES.index(MODULE.PHASE)
        ]:
            spec = VERIFY.PHASE_SPEC_BY_NAME[phase]
            claims = {
                claim: {
                    "value": rule_value(
                        phase,
                        claim,
                        rule,
                        self.manifest,
                    ),
                    "source_sha256": digest_for(
                        f"prior-source:{phase}:{claim}"
                    ),
                }
                for claim, rule in VERIFY.PHASE_CLAIM_RULES[
                    phase
                ].items()
            }
            document = {
                "schema": VERIFY.EVIDENCE_SCHEMA,
                "phase_evidence_schema_sha256": (
                    VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
                ),
                "campaign_id": self.manifest["campaign_id"],
                "operation_id": self.manifest["operation_id"],
                "release_sha": self.manifest["release_sha"],
                "legacy_release_sha": self.manifest[
                    "legacy_release_sha"
                ],
                "manifest_sha256": self.manifest_sha256,
                "plan_sha256": self.plan["plan_sha256"],
                "approval_sha256": self.manifest["artifacts"][
                    "cutover_approval_sha256"
                ],
                "manifest_artifact_bindings": dict(
                    self.manifest["artifacts"]
                ),
                "phase": phase,
                "operation": spec.operation,
                "journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
                "status": "passed",
                "captured_at": NOW.isoformat(),
                "business_write_observed": False,
                "prior_phase_evidence": [],
                "prior_phase_evidence_closure_sha256": digest_for(
                    f"prior-closure:{phase}"
                ),
                "prior_claim_bindings": [],
                "phase_input_closure_sha256": digest_for(
                    f"phase-input:{phase}"
                ),
                "role_attestations": [],
                "claims": claims,
            }
            self.assert_exact_evidence_fields(document)
            path = prior_root / f"{phase}.json"
            payload = canonical_bytes(document)
            path.write_bytes(payload)
            path.chmod(0o600)
            self.prior_paths[phase] = path
            self.prior_digests[phase] = hashlib.sha256(
                payload
            ).hexdigest()

    @staticmethod
    def assert_exact_evidence_fields(document: dict) -> None:
        if set(document) != VERIFY.EVIDENCE_FIELDS:
            raise AssertionError("test prior evidence fields differ")

    def _start_cutover_journal(self) -> None:
        journal = CONTROLLER.ProductionCutoverJournal(
            Path(
                self.manifest["deployment"][
                    "controller_journal_path"
                ]
            )
        )
        journal.create(
            manifest_sha256=self.manifest_sha256,
            plan_sha256=self.plan["plan_sha256"],
            campaign_id=self.manifest["campaign_id"],
            operation_id=self.manifest["operation_id"],
            release_sha=self.manifest["release_sha"],
            legacy_release_sha=self.manifest["legacy_release_sha"],
        )
        for index, phase in enumerate(
            CONTROLLER.PHASES[
                : CONTROLLER.PHASES.index(MODULE.PHASE)
            ],
            1,
        ):
            journal.begin_phase(phase)
            journal.complete_phase(
                phase,
                verification=CONTROLLER.VerifiedPhaseCompletion(
                    phase=phase,
                    evidence_sha256=self.prior_digests[phase],
                    receipt_sha256=digest_for(
                        f"verification:{index}:{phase}"
                    ),
                ),
            )
        journal.begin_phase(MODULE.PHASE)
        self.journal_path = journal.path

    def _write_role_validations(self) -> None:
        role_root = self.root / "roles"
        role_root.mkdir(mode=0o700)
        spec = VERIFY.PHASE_SPEC_BY_NAME[MODULE.PHASE]
        for role in spec.roles:
            topology = CONTROLLER.EXPECTED_TOPOLOGY[role]
            document = {
                "schema": "production-shadow-host-agent-validation-v1",
                "status": "validated-request",
                "request_sha256": digest_for(f"request:{role}"),
                "operation": spec.operation,
                "role": role,
                "campaign_id": self.manifest["campaign_id"],
                "operation_id": self.manifest["operation_id"],
                "app_release_sha": self.manifest["release_sha"],
                "manifest_sha256": self.manifest_sha256,
                "approval_sha256": self.manifest["artifacts"][
                    "cutover_approval_sha256"
                ],
                "expected_host": topology["host"],
                "observed_host": topology["host"],
                "required_journal_status": (
                    CONTROLLER.PRECOMMIT_JOURNAL_STATUS
                ),
                "business_write_policy": "forbid",
                "agent_artifact_sha256": self.manifest["artifacts"][
                    "host_agent_sha256"
                ],
                "host_agent_contract_sha256": self.manifest["artifacts"][
                    "host_agent_contract_sha256"
                ],
                "transport": topology["transport"],
                "observed_at": NOW.isoformat(),
                "host_identity_observed": True,
                "execution_supported": False,
                "production_contacted": False,
            }
            path = role_root / f"{role}.json"
            path.write_bytes(canonical_bytes(document))
            path.chmod(0o600)
            self.role_paths[role] = path

    def claim_value(self, claim: str) -> object:
        rule = VERIFY.PHASE_CLAIM_RULES[MODULE.PHASE][claim]
        values = {
            "restored_postgres_snapshot_set_sha256": (
                POSTGRES_SNAPSHOT_SET_SHA256
            ),
            "restored_reviewed_file_snapshot_set_sha256": (
                FILE_SNAPSHOT_SET_SHA256
            ),
            "inventory_closure_sha256": INVENTORY_CLOSURE_SHA256,
            "restore_result_set_sha256": RESTORE_RESULT_SET_SHA256,
        }
        return (
            rule.expected
            if rule.kind == "exact"
            else values[claim]
        )

    def _write_claim_sources(self) -> None:
        claim_root = self.root / "claims"
        claim_root.mkdir(mode=0o700)
        spec = VERIFY.PHASE_SPEC_BY_NAME[MODULE.PHASE]
        for claim in VERIFY.PHASE_CLAIM_RULES[MODULE.PHASE]:
            document = {
                "schema": "production-shadow-phase-claim-source-v1",
                "campaign_id": self.manifest["campaign_id"],
                "operation_id": self.manifest["operation_id"],
                "release_sha": self.manifest["release_sha"],
                "manifest_sha256": self.manifest_sha256,
                "phase": MODULE.PHASE,
                "operation": spec.operation,
                "claim": claim,
                "value": self.claim_value(claim),
                "observed_at": NOW.isoformat(),
                "status": "observed",
            }
            path = claim_root / f"{claim}.json"
            path.write_bytes(canonical_bytes(document))
            path.chmod(0o600)
            self.claim_paths[claim] = path

    def role_arguments(self) -> list[str]:
        return [
            f"{role}={self.role_paths[role]}"
            for role in VERIFY.PHASE_SPEC_BY_NAME[MODULE.PHASE].roles
        ]

    def claim_arguments(self) -> list[str]:
        return [
            f"{claim}={self.claim_paths[claim]}"
            for claim in VERIFY.PHASE_CLAIM_RULES[MODULE.PHASE]
        ]

    def prior_arguments(self) -> list[str]:
        return [
            f"{phase}={self.prior_paths[phase]}"
            for phase in CONTROLLER.PHASES[
                : CONTROLLER.PHASES.index(MODULE.PHASE)
            ]
        ]

    def execute(self, **overrides):
        arguments = {
            "manifest_path": self.manifest_path,
            "output_directory": self.output,
            "role_validation": self.role_arguments(),
            "claim_source": self.claim_arguments(),
            "prior_phase_evidence": self.prior_arguments(),
            "now": NOW,
        }
        arguments.update(overrides)
        return MODULE.execute(**arguments)

    def rewrite_claim(self, claim_name: str, **updates: object) -> None:
        path = self.claim_paths[claim_name]
        document = json.loads(path.read_text(encoding="utf-8"))
        document.update(updates)
        path.write_bytes(canonical_bytes(document))
        path.chmod(0o600)

    def rewrite_role(self, role_name: str, **updates: object) -> None:
        path = self.role_paths[role_name]
        document = json.loads(path.read_text(encoding="utf-8"))
        document.update(updates)
        path.write_bytes(canonical_bytes(document))
        path.chmod(0o600)


class FrozenFinalRestorePhaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        secure_root = lambda campaign_id: (  # noqa: E731
            PurePosixPath(self.root / "secure") / campaign_id
        )
        self.controller_secure_patch = mock.patch.object(
            CONTROLLER,
            "_secure_root",
            secure_root,
        )
        self.fixture_secure_patch = mock.patch(
            "tests.test_production_shadow_cutover_controller._secure_root",
            secure_root,
        )
        self.controller_secure_patch.start()
        self.fixture_secure_patch.start()
        self.fixture = PhaseEvidenceFixture(self.root)

    def test_completion_host_config_digest_is_mandatory_and_nonzero(self):
        completion = {
            "roles": {
                "bot_fi": {
                    "host_result": {
                        "action_evidence": {
                            "verify-final": {
                                "document": {
                                    "semantic": {
                                        "database_host_config_sha256": (
                                            "a" * 64
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        self.assertEqual(
            MODULE._completion_database_host_config_sha256(
                completion,
                "bot_fi",
            ),
            "a" * 64,
        )
        for value in (None, "0" * 64, "short"):
            with self.subTest(value=value):
                tampered = json.loads(json.dumps(completion))
                semantic = tampered["roles"]["bot_fi"]["host_result"][
                    "action_evidence"
                ]["verify-final"]["document"]["semantic"]
                if value is None:
                    del semantic["database_host_config_sha256"]
                else:
                    semantic["database_host_config_sha256"] = value
                with self.assertRaises(
                    MODULE.FrozenFinalRestorePhaseEvidenceError
                ):
                    MODULE._completion_database_host_config_sha256(
                        tampered,
                        "bot_fi",
                    )

    def tearDown(self) -> None:
        self.fixture_secure_patch.stop()
        self.controller_secure_patch.stop()
        self.temporary.cleanup()

    def mock_derived_inputs(self) -> MODULE.DerivedEvidenceInputs:
        derivation_path = self.root / "derivation.json"
        if not derivation_path.exists():
            derivation_path.write_bytes(b"{}\n")
            derivation_path.chmod(0o600)
        return MODULE.DerivedEvidenceInputs(
            derivation_path=derivation_path,
            derivation_sha256=hashlib.sha256(
                derivation_path.read_bytes()
            ).hexdigest(),
            manifest_path=self.fixture.manifest_path,
            output_directory=self.fixture.output,
            role_validation=tuple(self.fixture.role_arguments()),
            claim_source=tuple(self.fixture.claim_arguments()),
            prior_phase_evidence=tuple(self.fixture.prior_arguments()),
            role_source_sha256={
                role: hashlib.sha256(path.read_bytes()).hexdigest()
                for role, path in self.fixture.role_paths.items()
            },
            claim_source_sha256={
                claim: hashlib.sha256(path.read_bytes()).hexdigest()
                for claim, path in self.fixture.claim_paths.items()
            },
        )

    def test_plan_builds_exact_self_verified_evidence_without_mutation(self):
        journal_before = self.fixture.journal_path.read_bytes()
        result = self.fixture.execute()
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["publication"], "planned")
        self.assertEqual(result["self_verification_status"], "verified")
        self.assertFalse(result["output_mutated"])
        self.assertFalse(result["journal_mutated"])
        self.assertFalse(result["network_io"])
        self.assertFalse(result["docker_invoked"])
        self.assertFalse(result["ssh_invoked"])
        self.assertFalse(result["object_storage_contacted"])
        self.assertEqual(list(self.fixture.output.iterdir()), [])
        self.assertEqual(
            self.fixture.journal_path.read_bytes(),
            journal_before,
        )

        prepared = MODULE.prepare_evidence(
            manifest_path=self.fixture.manifest_path,
            output_directory=self.fixture.output,
            role_validation=self.fixture.role_arguments(),
            claim_source=self.fixture.claim_arguments(),
            prior_phase_evidence=self.fixture.prior_arguments(),
            now=NOW,
        )
        self.assertEqual(
            prepared.document["claims"][
                "restored_postgres_snapshot_set_sha256"
            ]["value"],
            POSTGRES_SNAPSHOT_SET_SHA256,
        )
        self.assertEqual(
            prepared.document["claims"][
                "restored_reviewed_file_snapshot_set_sha256"
            ]["value"],
            FILE_SNAPSHOT_SET_SHA256,
        )
        self.assertEqual(
            prepared.document["claims"]["restore_result_set_sha256"][
                "value"
            ],
            RESTORE_RESULT_SET_SHA256,
        )
        self.assertEqual(
            prepared.document["claims"][
                "non_operation_resource_delta_count"
            ]["value"],
            0,
        )
        self.assertEqual(
            [row["phase"] for row in prepared.document[
                "prior_phase_evidence"
            ]],
            list(
                CONTROLLER.PHASES[
                    : CONTROLLER.PHASES.index(MODULE.PHASE)
                ]
            ),
        )

    def test_standalone_apply_is_disabled_without_derivation(self):
        plan = self.fixture.execute()
        journal_before = self.fixture.journal_path.read_bytes()
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "standalone apply is disabled",
        ):
            self.fixture.execute(
                apply=True,
                confirm=plan["required_confirmation"],
            )
        self.assertEqual(self.fixture.journal_path.read_bytes(), journal_before)
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_verified_derivation_api_is_create_only_and_retry_safe(self):
        inputs = self.mock_derived_inputs()
        derivation_path = inputs.derivation_path
        derivation_sha256 = inputs.derivation_sha256
        journal_before = self.fixture.journal_path.read_bytes()
        with (
            mock.patch.object(
                MODULE,
                "_validated_derivation_inputs",
                return_value=inputs,
            ) as validate,
            mock.patch.object(MODULE, "_utc_now", return_value=NOW),
        ):
            planned = MODULE.execute_derived(
                derivation_path=derivation_path,
                derivation_sha256=derivation_sha256,
                now=NOW,
            )
            first = MODULE.execute_derived(
                derivation_path=derivation_path,
                derivation_sha256=derivation_sha256,
                apply=True,
                confirm=planned["required_confirmation"],
            )
            second = MODULE.execute_derived(
                derivation_path=derivation_path,
                derivation_sha256=derivation_sha256,
                apply=True,
                confirm=planned["required_confirmation"],
            )
        self.assertEqual(validate.call_count, 5)
        self.assertEqual(first["status"], "published")
        self.assertEqual(first["publication"], "created")
        self.assertTrue(first["output_mutated"])
        self.assertEqual(second["publication"], "reused")
        self.assertFalse(second["output_mutated"])
        self.assertEqual(first["evidence_sha256"], second["evidence_sha256"])
        self.assertEqual(first["derivation_sha256"], derivation_sha256)
        output = Path(first["output"])
        observed, digest = VERIFY.read_root_only_evidence(output)
        self.assertEqual(digest, first["evidence_sha256"])
        self.assertEqual(observed["phase"], MODULE.PHASE)
        self.assertEqual(self.fixture.journal_path.read_bytes(), journal_before)

    def test_live_derived_apply_rejects_caller_selected_clock(self):
        inputs = self.mock_derived_inputs()
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "does not accept a caller-selected clock",
        ):
            MODULE.execute_derived(
                derivation_path=inputs.derivation_path,
                derivation_sha256=inputs.derivation_sha256,
                apply=True,
                confirm="irrelevant",
                now=NOW,
            )
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_stale_role_observation_fails_closed(self):
        self.fixture.rewrite_role(
            "bot_fi",
            observed_at=(NOW - timedelta(hours=3)).isoformat(),
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "bot_fi role validation observation is not fresh",
        ):
            self.fixture.execute()
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_derived_completion_requires_distinct_wa_control_object(self):
        completion_sha256 = "9" * 64
        restore_set_sha256 = "8" * 64
        completion_path = self.root / f"completion-{completion_sha256}.json"
        requests = {
            role: RESTORE_TESTS.request_for(role)
            for role in MODULE.RESTORE.ROLES
        }
        manifest = {
            "campaign_id": RESTORE_TESTS.CAMPAIGN_ID,
            "operation_id": RESTORE_TESTS.OPERATION_ID,
            "release_sha": RESTORE_TESTS.RELEASE_SHA,
            "release_tree_sha": RESTORE_TESTS.RELEASE_TREE_SHA,
        }
        completion = {
            "schema": MODULE.RESTORE.COMPLETION_SCHEMA,
            "status": "three-role-frozen-final-restored",
            **manifest,
            "controller_manifest_sha256": RESTORE_TESTS.SHA_A,
            "restore_set_sha256": restore_set_sha256,
            "restore_generation_sha256": RESTORE_TESTS.SHA_C,
            "live_lease_claim_sha256": RESTORE_TESTS.SHA_C,
            "live_lease_claim_epoch": 4,
            "live_lease_claim_nonce": RESTORE_TESTS.SHA_B,
            "legacy_frozen_receipt_sha256": RESTORE_TESTS.SHA_D,
            "roles": {
                role: {
                    "source_role": role,
                    "transport": requests[role]["transport"],
                    "host_result": {},
                    "host_result_sha256": RESTORE_TESTS.SHA_A,
                    "role_manifest_sha256": RESTORE_TESTS.SHA_B,
                    "installer_receipt_sha256": RESTORE_TESTS.SHA_C,
                    "restore_result_sha256": RESTORE_TESTS.SHA_D,
                    "final_evidence_sha256": RESTORE_TESTS.SHA_E,
                }
                for role in MODULE.RESTORE.ROLES
            },
            "role_order": list(MODULE.RESTORE.ROLES),
            "claim_consume_outcome": (
                MODULE.WORKER.LIVE_LEASE_SUCCESS_OUTCOME
            ),
            "claim_consumed": False,
            "consumption_receipt_included": False,
            "current_mutated": False,
            "legacy_mutated": False,
            "object_storage_mutated_by_restore": False,
            "app_services_started": False,
            "redis_restored": False,
        }

        def secure_json(path, *, label, maximum=MODULE.MAX_JSON_BYTES):
            del maximum
            if label == "derived frozen restore completion":
                return completion, completion_sha256
            for role, request in requests.items():
                if label == f"{role} persisted restore request":
                    return request, digest_for(f"request:{role}")
            raise AssertionError((path, label))

        mutations = (
            (
                "version_id",
                lambda request: request[
                    "wa_fresh_control_exact_version"
                ].__setitem__(
                    "version_id",
                    request["wa_exact_version"]["version_id"],
                ),
            ),
            (
                "object_key",
                lambda request: request["wa_exact_version"].__setitem__(
                    "object_key",
                    request["wa_fresh_control_exact_version"]["object_key"],
                ),
            ),
            (
                "bucket",
                lambda request: request[
                    "wa_fresh_control_exact_version"
                ].__setitem__("bucket", "other-private-bucket"),
            ),
            (
                "recipient",
                lambda request: request[
                    "wa_fresh_control_exact_version"
                ].__setitem__("recipient", "age1" + "p" * 58),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                requests["webapp_ir"] = RESTORE_TESTS.request_for(
                    "webapp_ir"
                )
                mutate(requests["webapp_ir"])
                restore_set = {
                    "restore_generation_sha256": RESTORE_TESTS.SHA_C,
                    "webapp_ir_transport": dict(
                        requests["webapp_ir"]["wa_exact_version"]
                    ),
                }
                with (
                    mock.patch.object(
                        MODULE,
                        "_secure_json",
                        side_effect=secure_json,
                    ),
                    self.assertRaisesRegex(
                        MODULE.FrozenFinalRestorePhaseEvidenceError,
                        "fresh-control object is not new and distinct",
                    ),
                ):
                    MODULE._validate_completion(  # noqa: SLF001
                        completion_path,
                        completion_sha256,
                        manifest=manifest,
                        manifest_sha256=RESTORE_TESTS.SHA_A,
                        restore_set=restore_set,
                        restore_set_sha256=restore_set_sha256,
                    )

    def test_nonzero_non_operation_delta_fails_before_any_mutation(self):
        journal_before = self.fixture.journal_path.read_bytes()
        self.fixture.rewrite_claim(
            "non_operation_resource_delta_count",
            value=1,
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "normalized claim source is invalid",
        ):
            self.fixture.execute()
        self.assertEqual(list(self.fixture.output.iterdir()), [])
        self.assertEqual(self.fixture.journal_path.read_bytes(), journal_before)

    def test_missing_or_mismatched_prior_phase_fails_closed(self):
        missing = self.fixture.prior_arguments()[:-1]
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "mapping is not exact",
        ):
            self.fixture.execute(prior_phase_evidence=missing)
        self.assertEqual(list(self.fixture.output.iterdir()), [])

        phase = CONTROLLER.PHASES[0]
        path = self.fixture.prior_paths[phase]
        path.write_bytes(path.read_bytes() + b" ")
        path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "differs from the journal",
        ):
            self.fixture.execute()
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_wrong_role_or_claim_source_identity_fails_closed(self):
        self.fixture.rewrite_role("bot_fi", role="webapp_fi")
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "role validation.*invalid",
        ):
            self.fixture.execute()
        self.assertEqual(list(self.fixture.output.iterdir()), [])

        self.fixture.rewrite_role("bot_fi", role="bot_fi")
        self.fixture.rewrite_claim(
            "postgres_restore_verified",
            claim="reviewed_file_restore_verified",
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "normalized claim source is invalid",
        ):
            self.fixture.execute()
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_stale_source_and_unsafe_source_files_are_rejected(self):
        stale = (NOW - timedelta(hours=3)).isoformat()
        self.fixture.rewrite_claim(
            "restore_result_set_sha256",
            observed_at=stale,
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "normalized claim source is invalid",
        ):
            self.fixture.execute()

        self.fixture.rewrite_claim(
            "restore_result_set_sha256",
            observed_at=NOW.isoformat(),
        )
        target = self.fixture.claim_paths["restore_result_set_sha256"]
        target.chmod(0o640)
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "normalized claim source is invalid",
        ):
            self.fixture.execute()

        target.chmod(0o600)
        link = target.with_name("claim-link.json")
        link.symlink_to(target)
        arguments = self.fixture.claim_arguments()
        arguments = [
            (
                f"restore_result_set_sha256={link}"
                if value.startswith("restore_result_set_sha256=")
                else value
            )
            for value in arguments
        ]
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "normalized claim source is invalid",
        ):
            self.fixture.execute(claim_source=arguments)

        link.unlink()
        link = target.with_name("claim-hardlink.json")
        os.link(target, link)
        arguments = self.fixture.claim_arguments()
        arguments = [
            (
                f"restore_result_set_sha256={link}"
                if value.startswith("restore_result_set_sha256=")
                else value
            )
            for value in arguments
        ]
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "normalized claim source is invalid",
        ):
            self.fixture.execute(claim_source=arguments)

    def test_existing_different_digest_path_is_never_overwritten(self):
        prepared = MODULE.prepare_evidence(
            manifest_path=self.fixture.manifest_path,
            output_directory=self.fixture.output,
            role_validation=self.fixture.role_arguments(),
            claim_source=self.fixture.claim_arguments(),
            prior_phase_evidence=self.fixture.prior_arguments(),
            now=NOW,
        )
        prepared.output.write_bytes(b"different\n")
        prepared.output.chmod(0o600)
        before = prepared.output.read_bytes()
        directory_ctime_ns = self.fixture.output.stat().st_ctime_ns
        inputs = self.mock_derived_inputs()
        with (
            mock.patch.object(
                MODULE,
                "_validated_derivation_inputs",
                return_value=inputs,
            ),
            mock.patch.object(MODULE, "_utc_now", return_value=NOW),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestorePhaseEvidenceError,
                "existing digest-derived phase evidence differs",
            ),
        ):
            MODULE._publish_derived(  # noqa: SLF001
                prepared,
                inputs,
            )
        self.assertEqual(prepared.output.read_bytes(), before)
        self.assertEqual(
            self.fixture.output.stat().st_ctime_ns,
            directory_ctime_ns,
        )

    def test_publication_revalidates_normalized_sources(self):
        prepared = MODULE.prepare_evidence(
            manifest_path=self.fixture.manifest_path,
            output_directory=self.fixture.output,
            role_validation=self.fixture.role_arguments(),
            claim_source=self.fixture.claim_arguments(),
            prior_phase_evidence=self.fixture.prior_arguments(),
            now=NOW,
        )
        inputs = self.mock_derived_inputs()
        self.fixture.rewrite_claim(
            "restore_result_set_sha256",
            observed_at=(NOW - timedelta(minutes=1)).isoformat(),
        )
        with (
            mock.patch.object(
                MODULE,
                "_validated_derivation_inputs",
                return_value=inputs,
            ),
            mock.patch.object(MODULE, "_utc_now", return_value=NOW),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestorePhaseEvidenceError,
                "prepared evidence differs from its derivation source hashes",
            ),
        ):
            MODULE._publish_derived(  # noqa: SLF001
                prepared,
                inputs,
            )
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_writer_revalidation_uses_its_own_live_clock(self):
        stale_now = NOW - timedelta(hours=3)
        for role in self.fixture.role_paths:
            self.fixture.rewrite_role(
                role,
                observed_at=stale_now.isoformat(),
            )
        for claim in self.fixture.claim_paths:
            self.fixture.rewrite_claim(
                claim,
                observed_at=stale_now.isoformat(),
            )
        prepared = MODULE.prepare_evidence(
            manifest_path=self.fixture.manifest_path,
            output_directory=self.fixture.output,
            role_validation=self.fixture.role_arguments(),
            claim_source=self.fixture.claim_arguments(),
            prior_phase_evidence=self.fixture.prior_arguments(),
            now=stale_now,
        )
        inputs = self.mock_derived_inputs()
        with (
            mock.patch.object(
                MODULE,
                "_validated_derivation_inputs",
                return_value=inputs,
            ),
            mock.patch.object(MODULE, "_utc_now", return_value=NOW),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestorePhaseEvidenceError,
                "role validation or normalized claim source is invalid",
            ),
        ):
            MODULE._publish_derived(prepared, inputs)  # noqa: SLF001
        self.assertEqual(list(self.fixture.output.iterdir()), [])

    def test_wrong_confirmation_and_non_root_are_non_mutating(self):
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestorePhaseEvidenceError,
            "standalone apply is disabled",
        ):
            self.fixture.execute(apply=True, confirm="wrong")
        self.assertEqual(list(self.fixture.output.iterdir()), [])

        with (
            mock.patch.object(MODULE.os, "geteuid", return_value=1000),
            self.assertRaisesRegex(
                MODULE.FrozenFinalRestorePhaseEvidenceError,
                "must run as root",
            ),
        ):
            self.fixture.execute()
        self.assertEqual(list(self.fixture.output.iterdir()), [])


def stat_mode(path: Path) -> int:
    return path.stat(follow_symlinks=False).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
