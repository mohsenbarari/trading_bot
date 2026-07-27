from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.production_shadow_cutover_controller import (
    EXPECTED_TOPOLOGY,
    PHASES,
    PHASE_SPECS,
    PRECOMMIT_JOURNAL_STATUS,
)
from scripts.verify_production_shadow_phase_evidence import (
    EVIDENCE_SCHEMA,
    PHASE_CLAIM_RULES,
    PHASE_EVIDENCE_CONTRACT,
    PHASE_EVIDENCE_CONTRACT_SHA256,
    PHASE_MANIFEST_CLAIM_BINDINGS,
    PHASE_PRIOR_CLAIM_BINDINGS,
    PhaseEvidenceError,
    _read_claim_source_records,
    _read_role_validation_records,
    main,
    read_root_only_evidence,
    verify_phase_evidence,
)
from tests.test_production_shadow_cutover_controller import manifest_payload


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
LEGACY_RELEASE_SHA = "b" * 40
MANIFEST_SHA256 = "c" * 64
PLAN_SHA256 = "d" * 64
APPROVAL_SHA256 = "e" * 64
MANIFEST_ARTIFACTS = dict(manifest_payload()["artifacts"])
MANIFEST_ARTIFACTS["cutover_approval_sha256"] = APPROVAL_SHA256
MANIFEST_ARTIFACTS[
    "phase_evidence_schema_sha256"
] = PHASE_EVIDENCE_CONTRACT_SHA256


def _rule_value(rule):  # noqa: ANN001, ANN202
    if rule.kind == "exact":
        return rule.expected
    if rule.kind == "nonzero-sha256":
        return "f" * 64
    if rule.kind == "positive-int":
        return 1
    if rule.kind == "immutable-image-id":
        return "sha256:" + "f" * 64
    if rule.kind == "nonempty-string":
        return "value"
    raise AssertionError(rule.kind)


def expected_dynamic_claims(phase: str) -> dict:
    bindings = PHASE_MANIFEST_CLAIM_BINDINGS.get(phase, {})
    result = {
        name: (
            MANIFEST_ARTIFACTS[bindings[name]]
            if name in bindings
            else _rule_value(rule)
        )
        for name, rule in PHASE_CLAIM_RULES[phase].items()
        if rule.kind != "exact"
    }
    for target, (source_phase, source_claim) in PHASE_PRIOR_CLAIM_BINDINGS.get(
        phase,
        {},
    ).items():
        source_rule = PHASE_CLAIM_RULES[source_phase][source_claim]
        result[target] = (
            source_rule.expected
            if source_rule.kind == "exact"
            else expected_dynamic_claims(source_phase)[source_claim]
        )
    return result


def expected_prior_evidence(phase: str) -> dict[str, str]:
    return {
        prior: hashlib.sha256(prior.encode("ascii")).hexdigest()
        for prior in PHASES[: PHASES.index(phase)]
    }


def prior_rows(phase: str) -> list[dict[str, str]]:
    expected = expected_prior_evidence(phase)
    return [
        {"phase": prior, "evidence_sha256": expected[prior]}
        for prior in PHASES[: PHASES.index(phase)]
    ]


def expected_prior_claim_values(phase: str) -> dict[str, object]:
    dynamic = expected_dynamic_claims(phase)
    return {
        target: dynamic[target]
        for target in PHASE_PRIOR_CLAIM_BINDINGS.get(phase, {})
    }


def prior_claim_rows(phase: str) -> list[dict[str, object]]:
    prior = expected_prior_evidence(phase)
    values = expected_prior_claim_values(phase)
    return [
        {
            "target_claim": target,
            "source_phase": source_phase,
            "source_claim": source_claim,
            "source_evidence_sha256": prior[source_phase],
            "value": values[target],
        }
        for target, (source_phase, source_claim) in sorted(
            PHASE_PRIOR_CLAIM_BINDINGS.get(phase, {}).items()
        )
    ]


def prior_evidence_records(
    phase: str,
    *,
    captured_at: datetime,
) -> dict[str, dict[str, object]]:
    expected = expected_prior_evidence(phase)
    return {
        prior: {
            "document": evidence_for(prior, captured_at=captured_at),
            "file_sha256": expected[prior],
        }
        for prior in PHASES[: PHASES.index(phase)]
    }


def evidence_for(phase: str, *, captured_at: datetime) -> dict:
    spec = next(spec for spec in PHASE_SPECS if spec.phase == phase)
    timestamp = captured_at.astimezone(timezone.utc).isoformat()
    prior = prior_rows(phase)
    prior_claims = prior_claim_rows(phase)
    dynamic = expected_dynamic_claims(phase)
    role_requests = {role: "1" * 64 for role in spec.roles}
    role_sources = {role: "2" * 64 for role in spec.roles}
    role_observed_at = {role: timestamp for role in spec.roles}
    claim_sources = {
        name: "3" * 64 for name in PHASE_CLAIM_RULES[phase]
    }
    phase_input = {
        "manifest_sha256": MANIFEST_SHA256,
        "manifest_artifacts_sha256": hashlib.sha256(
            json.dumps(
                MANIFEST_ARTIFACTS,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "prior_phase_evidence": prior,
        "prior_claim_bindings": prior_claims,
        "dynamic_claim_values": dynamic,
        "claim_source_sha256": {
            name: claim_sources[name] for name in sorted(claim_sources)
        },
        "role_request_sha256": role_requests,
        "role_source_artifact_sha256": role_sources,
        "role_observed_at": role_observed_at,
    }
    return {
        "schema": EVIDENCE_SCHEMA,
        "phase_evidence_schema_sha256": PHASE_EVIDENCE_CONTRACT_SHA256,
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "legacy_release_sha": LEGACY_RELEASE_SHA,
        "manifest_sha256": MANIFEST_SHA256,
        "plan_sha256": PLAN_SHA256,
        "approval_sha256": APPROVAL_SHA256,
        "manifest_artifact_bindings": dict(MANIFEST_ARTIFACTS),
        "phase": spec.phase,
        "operation": spec.operation,
        "journal_status": PRECOMMIT_JOURNAL_STATUS,
        "status": "passed",
        "captured_at": timestamp,
        "business_write_observed": False,
        "prior_phase_evidence": prior,
        "prior_phase_evidence_closure_sha256": hashlib.sha256(
            json.dumps(
                prior,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "prior_claim_bindings": prior_claims,
        "phase_input_closure_sha256": hashlib.sha256(
            json.dumps(
                phase_input,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "role_attestations": [
            {
                "role": role,
                "expected_host": EXPECTED_TOPOLOGY[role]["host"],
                "operation": spec.operation,
                "request_sha256": "1" * 64,
                "app_release_sha": RELEASE_SHA,
                "agent_artifact_sha256": MANIFEST_ARTIFACTS[
                    "host_agent_sha256"
                ],
                "host_identity_observed": True,
                "observed_at": timestamp,
                "status": "verified",
                "transport": EXPECTED_TOPOLOGY[role]["transport"],
                "source_artifact_sha256": "2" * 64,
            }
            for role in spec.roles
        ],
        "claims": {
            name: {
                "value": (
                    rule.expected
                    if rule.kind == "exact"
                    else dynamic[name]
                ),
                "source_sha256": "3" * 64,
            }
            for name, rule in PHASE_CLAIM_RULES[phase].items()
        },
    }


def verify(
    document: dict,
    *,
    phase: str,
    now: datetime,
    dynamic_claims: dict | None = None,
    prior_evidence: dict[str, str] | None = None,
    prior_records: dict[str, dict[str, object]] | None = None,
    role_observed_at: dict[str, str] | None = None,
) -> dict:
    spec = next(spec for spec in PHASE_SPECS if spec.phase == phase)
    return verify_phase_evidence(
        document,
        expected_phase=phase,
        expected_campaign_id=CAMPAIGN_ID,
        expected_operation_id=OPERATION_ID,
        expected_release_sha=RELEASE_SHA,
        expected_legacy_release_sha=LEGACY_RELEASE_SHA,
        expected_manifest_sha256=MANIFEST_SHA256,
        expected_plan_sha256=PLAN_SHA256,
        expected_approval_sha256=APPROVAL_SHA256,
        expected_phase_evidence_schema_sha256=(
            PHASE_EVIDENCE_CONTRACT_SHA256
        ),
        expected_manifest_artifacts=dict(MANIFEST_ARTIFACTS),
        expected_role_request_sha256={
            role: "1" * 64 for role in spec.roles
        },
        expected_role_source_artifact_sha256={
            role: "2" * 64 for role in spec.roles
        },
        expected_role_observed_at={
            role: document["captured_at"] for role in spec.roles
        }
        if role_observed_at is None
        else role_observed_at,
        expected_dynamic_claim_values=(
            expected_dynamic_claims(phase)
            if dynamic_claims is None
            else dynamic_claims
        ),
        expected_claim_source_sha256={
            name: "3" * 64 for name in PHASE_CLAIM_RULES[phase]
        },
        expected_prior_phase_evidence_sha256=(
            expected_prior_evidence(phase)
            if prior_evidence is None
            else prior_evidence
        ),
        prior_phase_evidence_records=(
            prior_evidence_records(phase, captured_at=now)
            if prior_records is None
            else prior_records
        ),
        now=now,
    )


class ProductionShadowPhaseEvidenceTests(unittest.TestCase):
    def test_contract_hash_is_canonical_and_covers_every_precommit_phase(self):
        expected = hashlib.sha256(
            json.dumps(
                PHASE_EVIDENCE_CONTRACT,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(PHASE_EVIDENCE_CONTRACT_SHA256, expected)
        self.assertEqual(
            list(PHASE_CLAIM_RULES),
            [spec.phase for spec in PHASE_SPECS],
        )

    def test_every_precommit_phase_exact_evidence_verifies(self):
        now = datetime.now(timezone.utc)
        for spec in PHASE_SPECS:
            with self.subTest(phase=spec.phase):
                result = verify(
                    evidence_for(spec.phase, captured_at=now),
                    phase=spec.phase,
                    now=now,
                )
                self.assertEqual(result["status"], "verified")
                self.assertEqual(result["phase"], spec.phase)
                self.assertEqual(result["operation"], spec.operation)
                self.assertEqual(result["verified_roles"], list(spec.roles))
                self.assertEqual(
                    result["verified_claim_count"],
                    len(PHASE_CLAIM_RULES[spec.phase]),
                )
                self.assertEqual(
                    result["prior_phase_count"],
                    PHASES.index(spec.phase),
                )
                self.assertFalse(result["production_contacted"])

    def test_binding_status_and_business_write_tampering_fail_closed(self):
        now = datetime.now(timezone.utc)
        phase = PHASE_SPECS[-1].phase
        base = evidence_for(phase, captured_at=now)
        mutations = {
            "campaign_id": OPERATION_ID,
            "release_sha": LEGACY_RELEASE_SHA,
            "manifest_sha256": "9" * 64,
            "phase": PHASE_SPECS[0].phase,
            "operation": PHASE_SPECS[0].operation,
            "journal_status": "forward-only-committed",
            "status": "planned",
            "business_write_observed": True,
            "phase_evidence_schema_sha256": "8" * 64,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                candidate = dict(base)
                candidate[field] = value
                with self.assertRaises(PhaseEvidenceError):
                    verify(candidate, phase=phase, now=now)

    def test_manifest_dynamic_claim_and_prior_chain_are_independently_bound(self):
        now = datetime.now(timezone.utc)
        freeze_phase = "freeze_generation_install"
        freeze = evidence_for(freeze_phase, captured_at=now)

        wrong_artifacts = json.loads(json.dumps(freeze))
        wrong_artifacts["manifest_artifact_bindings"][
            "nginx_freeze_generation_sha256"
        ] = "9" * 64
        with self.assertRaisesRegex(
            PhaseEvidenceError,
            "phase input closure|manifest artifact",
        ):
            verify(wrong_artifacts, phase=freeze_phase, now=now)

        wrong_claim = json.loads(json.dumps(freeze))
        wrong_claim["claims"]["manifest_freeze_generation_sha256"][
            "value"
        ] = "9" * 64
        changed_dynamic = expected_dynamic_claims(freeze_phase)
        changed_dynamic["manifest_freeze_generation_sha256"] = "9" * 64
        with self.assertRaisesRegex(
            PhaseEvidenceError,
            "phase input closure|manifest artifact",
        ):
            verify(
                wrong_claim,
                phase=freeze_phase,
                now=now,
                dynamic_claims=changed_dynamic,
            )

        final_phase = "pre_first_write_acceptance"
        acceptance = evidence_for(final_phase, captured_at=now)
        acceptance["prior_phase_evidence"][0]["evidence_sha256"] = "9" * 64
        with self.assertRaisesRegex(PhaseEvidenceError, "ordered journal prefix"):
            verify(acceptance, phase=final_phase, now=now)

        missing_prior = expected_prior_evidence(final_phase)
        missing_prior.pop(PHASES[0])
        with self.assertRaisesRegex(PhaseEvidenceError, "mapping"):
            verify(
                evidence_for(final_phase, captured_at=now),
                phase=final_phase,
                now=now,
                prior_evidence=missing_prior,
            )

    def test_role_order_host_transport_agent_identity_and_source_are_exact(self):
        now = datetime.now(timezone.utc)
        phase = "pre_freeze_evidence"
        base = evidence_for(phase, captured_at=now)

        reversed_roles = json.loads(json.dumps(base))
        reversed_roles["role_attestations"].reverse()
        with self.assertRaisesRegex(PhaseEvidenceError, "attestation"):
            verify(reversed_roles, phase=phase, now=now)

        for field, value in (
            ("expected_host", "127.0.0.1"),
            ("transport", "scp"),
            ("operation", "unknown"),
            ("request_sha256", "0" * 64),
            ("source_artifact_sha256", "0" * 64),
            ("app_release_sha", LEGACY_RELEASE_SHA),
            ("agent_artifact_sha256", "9" * 64),
            ("host_identity_observed", False),
        ):
            with self.subTest(field=field):
                candidate = json.loads(json.dumps(base))
                candidate["role_attestations"][0][field] = value
                with self.assertRaises(PhaseEvidenceError):
                    verify(candidate, phase=phase, now=now)

    def test_every_prior_claim_binding_is_extracted_from_prior_evidence(self):
        now = datetime.now(timezone.utc)
        for phase, bindings in PHASE_PRIOR_CLAIM_BINDINGS.items():
            for target, (source_phase, source_claim) in bindings.items():
                with self.subTest(phase=phase, target=target):
                    records = prior_evidence_records(phase, captured_at=now)
                    records[source_phase]["document"]["claims"][source_claim][
                        "value"
                    ] = "9" * 64
                    with self.assertRaisesRegex(
                        PhaseEvidenceError,
                        "prior claim bindings|prior verified claim|phase input closure",
                    ):
                        verify(
                            evidence_for(phase, captured_at=now),
                            phase=phase,
                            now=now,
                            prior_records=records,
                        )

    def test_claim_set_type_value_source_and_expected_input_are_exact(self):
        now = datetime.now(timezone.utc)
        phase = "precommit_provider_free_queue_rehydrate"
        base = evidence_for(phase, captured_at=now)

        missing = json.loads(json.dumps(base))
        missing["claims"].pop("provider_egress_attempt_count")
        with self.assertRaisesRegex(PhaseEvidenceError, "claim set"):
            verify(missing, phase=phase, now=now)

        extra = json.loads(json.dumps(base))
        extra["claims"]["unreviewed"] = {
            "value": True,
            "source_sha256": "4" * 64,
        }
        with self.assertRaisesRegex(PhaseEvidenceError, "claim set"):
            verify(extra, phase=phase, now=now)

        wrong_value = json.loads(json.dumps(base))
        wrong_value["claims"]["provider_egress_attempt_count"]["value"] = 1
        with self.assertRaisesRegex(PhaseEvidenceError, "required value"):
            verify(wrong_value, phase=phase, now=now)

        bool_as_int = json.loads(json.dumps(base))
        bool_as_int["claims"]["provider_egress_attempt_count"]["value"] = False
        with self.assertRaisesRegex(PhaseEvidenceError, "required value"):
            verify(bool_as_int, phase=phase, now=now)

        no_source = json.loads(json.dumps(base))
        no_source["claims"]["provider_egress_attempt_count"][
            "source_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(PhaseEvidenceError, "source"):
            verify(no_source, phase=phase, now=now)

        dynamic_phase = "precommit_irreversible_effect_watchers"
        dynamic_base = evidence_for(dynamic_phase, captured_at=now)
        wrong_dynamic = expected_dynamic_claims(dynamic_phase)
        wrong_dynamic["watcher_baseline_set_sha256"] = "9" * 64
        with self.assertRaisesRegex(
            PhaseEvidenceError,
            "phase input closure|expected input",
        ):
            verify(
                dynamic_base,
                phase=dynamic_phase,
                now=now,
                dynamic_claims=wrong_dynamic,
            )

    def test_secure_role_and_claim_source_files_drive_expected_inputs(self):
        now = datetime.now(timezone.utc)
        phase = "pre_freeze_evidence"
        spec = next(spec for spec in PHASE_SPECS if spec.phase == phase)
        manifest = manifest_payload()
        manifest["artifacts"] = dict(MANIFEST_ARTIFACTS)
        role_args: list[str] = []
        claim_args: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for role in spec.roles:
                topology = EXPECTED_TOPOLOGY[role]
                record = {
                    "schema": "production-shadow-host-agent-validation-v1",
                    "status": "validated-request",
                    "request_sha256": "1" * 64,
                    "operation": spec.operation,
                    "role": role,
                    "campaign_id": CAMPAIGN_ID,
                    "operation_id": OPERATION_ID,
                    "app_release_sha": RELEASE_SHA,
                    "manifest_sha256": MANIFEST_SHA256,
                    "approval_sha256": APPROVAL_SHA256,
                    "expected_host": topology["host"],
                    "observed_host": topology["host"],
                    "required_journal_status": PRECOMMIT_JOURNAL_STATUS,
                    "business_write_policy": "forbid",
                    "agent_artifact_sha256": MANIFEST_ARTIFACTS[
                        "host_agent_sha256"
                    ],
                    "host_agent_contract_sha256": MANIFEST_ARTIFACTS[
                        "host_agent_contract_sha256"
                    ],
                    "transport": topology["transport"],
                    "observed_at": now.isoformat(),
                    "host_identity_observed": True,
                    "execution_supported": False,
                    "production_contacted": False,
                }
                path = root / f"{role}.json"
                path.write_text(json.dumps(record), encoding="utf-8")
                path.chmod(0o600)
                role_args.append(f"{role}={path}")
            request_hashes, source_hashes, observed = (
                _read_role_validation_records(
                    role_args,
                    phase=phase,
                    manifest=manifest,
                    manifest_sha256=MANIFEST_SHA256,
                )
            )
            self.assertEqual(
                request_hashes,
                {role: "1" * 64 for role in spec.roles},
            )
            self.assertEqual(set(source_hashes), set(spec.roles))
            self.assertEqual(
                observed,
                {role: now.isoformat() for role in spec.roles},
            )

            expected_dynamic = expected_dynamic_claims(phase)
            for claim, rule in PHASE_CLAIM_RULES[phase].items():
                value = (
                    rule.expected
                    if rule.kind == "exact"
                    else expected_dynamic[claim]
                )
                record = {
                    "schema": "production-shadow-phase-claim-source-v1",
                    "campaign_id": CAMPAIGN_ID,
                    "operation_id": OPERATION_ID,
                    "release_sha": RELEASE_SHA,
                    "manifest_sha256": MANIFEST_SHA256,
                    "phase": phase,
                    "operation": spec.operation,
                    "claim": claim,
                    "value": value,
                    "observed_at": now.isoformat(),
                    "status": "observed",
                }
                path = root / f"claim-{claim}.json"
                path.write_text(json.dumps(record), encoding="utf-8")
                path.chmod(0o600)
                claim_args.append(f"{claim}={path}")
            dynamic, claim_hashes = _read_claim_source_records(
                claim_args,
                phase=phase,
                manifest=manifest,
                manifest_sha256=MANIFEST_SHA256,
                now=now,
            )
            self.assertEqual(dynamic, expected_dynamic)
            self.assertEqual(set(claim_hashes), set(PHASE_CLAIM_RULES[phase]))

    def test_phase_specific_freshness_and_cross_role_skew_fail_closed(self):
        now = datetime.now(timezone.utc)
        phase = "pre_freeze_evidence"

        stale = evidence_for(phase, captured_at=now - timedelta(hours=3))
        with self.assertRaisesRegex(PhaseEvidenceError, "stale"):
            verify(stale, phase=phase, now=now)

        future = evidence_for(phase, captured_at=now + timedelta(minutes=2))
        with self.assertRaisesRegex(PhaseEvidenceError, "future"):
            verify(future, phase=phase, now=now)

        skewed = evidence_for(phase, captured_at=now)
        skewed_at = (
            now - timedelta(hours=1)
        ).isoformat()
        skewed["role_attestations"][0]["observed_at"] = skewed_at
        role_times = {
            attestation["role"]: attestation["observed_at"]
            for attestation in skewed["role_attestations"]
        }
        phase_input = {
            "manifest_sha256": MANIFEST_SHA256,
            "manifest_artifacts_sha256": hashlib.sha256(
                json.dumps(
                    MANIFEST_ARTIFACTS,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "prior_phase_evidence": skewed["prior_phase_evidence"],
            "prior_claim_bindings": skewed["prior_claim_bindings"],
            "dynamic_claim_values": expected_dynamic_claims(phase),
            "claim_source_sha256": {
                name: "3" * 64
                for name in sorted(PHASE_CLAIM_RULES[phase])
            },
            "role_request_sha256": {
                attestation["role"]: attestation["request_sha256"]
                for attestation in skewed["role_attestations"]
            },
            "role_source_artifact_sha256": {
                attestation["role"]: attestation["source_artifact_sha256"]
                for attestation in skewed["role_attestations"]
            },
            "role_observed_at": role_times,
        }
        skewed["phase_input_closure_sha256"] = hashlib.sha256(
            json.dumps(
                phase_input,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(PhaseEvidenceError, "capture skew"):
            verify(
                skewed,
                phase=phase,
                now=now,
                role_observed_at=role_times,
            )

        acceptance_phase = "pre_first_write_acceptance"
        acceptance = evidence_for(
            acceptance_phase,
            captured_at=now - timedelta(minutes=6),
        )
        with self.assertRaisesRegex(PhaseEvidenceError, "stale"):
            verify(acceptance, phase=acceptance_phase, now=now)

    def test_secure_reader_rejects_mode_symlink_and_duplicate_json_fields(self):
        now = datetime.now(timezone.utc)
        payload = evidence_for("pre_freeze_evidence", captured_at=now)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)
            loaded, digest = read_root_only_evidence(
                path,
                owner_uid=os.geteuid(),
            )
            self.assertEqual(loaded["phase"], "pre_freeze_evidence")
            self.assertEqual(len(digest), 64)

            path.chmod(0o640)
            with self.assertRaisesRegex(PhaseEvidenceError, "secure strict JSON"):
                read_root_only_evidence(path, owner_uid=os.geteuid())
            path.chmod(0o600)

            link = Path(directory) / "evidence-link.json"
            link.symlink_to(path)
            with self.assertRaisesRegex(PhaseEvidenceError, "secure strict JSON"):
                read_root_only_evidence(link, owner_uid=os.geteuid())

            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
            duplicate.chmod(0o600)
            with self.assertRaisesRegex(PhaseEvidenceError, "duplicate"):
                read_root_only_evidence(
                    duplicate,
                    owner_uid=os.geteuid(),
                )

    @unittest.skipUnless(os.geteuid() == 0, "production verifier is root-only")
    def test_cli_derives_manifest_plan_approval_journal_and_source_bindings(self):
        now = datetime.now(timezone.utc)
        phase = "pre_freeze_evidence"
        payload = evidence_for(phase, captured_at=now)
        spec = next(spec for spec in PHASE_SPECS if spec.phase == phase)
        manifest = manifest_payload()
        manifest["artifacts"] = dict(MANIFEST_ARTIFACTS)
        journal_state = {
            "status": "phase_started",
            "started_phase": phase,
            "completed_phases": [],
            "phase_evidence_sha256": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            raw = json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n"
            path.write_bytes(raw)
            path.chmod(0o600)
            argv = [
                "--evidence",
                str(path),
                "--expected-phase",
                phase,
                "--manifest",
                "/root/manifest.json",
                "--approval",
                "/root/approval.json",
            ]
            output = io.StringIO()
            journal = mock.Mock(unsafe=True)
            journal.assert_bindings.return_value = journal_state
            with (
                mock.patch(
                    "scripts.verify_production_shadow_phase_evidence.read_root_only_manifest",
                    return_value=(manifest, MANIFEST_SHA256),
                ),
                mock.patch(
                    "scripts.verify_production_shadow_phase_evidence.render_plan",
                    return_value={"plan_sha256": PLAN_SHA256},
                ),
                mock.patch(
                    "scripts.verify_production_shadow_phase_evidence.sha256_secure_file",
                    return_value=(APPROVAL_SHA256, 1),
                ),
                mock.patch(
                    "scripts.verify_production_shadow_phase_evidence.hash_release_verifier",
                    return_value=MANIFEST_ARTIFACTS[
                        "phase_evidence_verifier_sha256"
                    ],
                ),
                mock.patch(
                    "scripts.verify_production_shadow_phase_evidence.ProductionCutoverJournal",
                    return_value=journal,
                ),
                mock.patch(
                    "scripts.verify_production_shadow_phase_evidence._read_role_validation_records",
                    return_value=(
                        {role: "1" * 64 for role in spec.roles},
                        {role: "2" * 64 for role in spec.roles},
                        {role: payload["captured_at"] for role in spec.roles},
                    ),
                ),
                mock.patch(
                    "scripts.verify_production_shadow_phase_evidence._read_claim_source_records",
                    return_value=(
                        expected_dynamic_claims(phase),
                        {
                            name: "3" * 64
                            for name in PHASE_CLAIM_RULES[phase]
                        },
                    ),
                ),
                redirect_stdout(output),
            ):
                exit_code = main(argv)
            self.assertEqual(exit_code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "verified")
            self.assertEqual(
                result["evidence_sha256"],
                hashlib.sha256(raw).hexdigest(),
            )
            self.assertFalse(result["production_contacted"])


if __name__ == "__main__":
    unittest.main()
