from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from core.dr_event_protocol import canonical_json_bytes
from core.human_approval import POLICY_SCHEMA, approval_subject
from core.human_approval_issuer import (
    authenticate_and_issue,
    create_enrollment,
    totp_code,
)
from core.three_site_full_matrix_campaign import (
    BOUND_ARTIFACTS,
    CAMPAIGN_SCHEMA,
    CUSTOMER_ACTOR_PAIR_POLICIES,
    CUSTOMER_LIFECYCLE_MATRIX,
    PHASES,
    PHASE_EVIDENCE_SCHEMA,
    PHASE_SCENARIOS,
    SCENARIO_EVIDENCE_SCHEMA,
    FullMatrixCampaignError,
    _matrix_operation_id,
    customer_actor_pair_assertion_name,
    customer_actor_pair_contracts,
    verify_campaign,
    verify_complete_matrix,
    verify_scenario_evidence,
    scenarios_for_execution_class,
)
from core.three_site_execution_safety import (
    DEDICATED_HOST_DESTRUCTIVE,
    SHARED_HOST_SAFE,
)
from core.three_site_sync_timing import (
    SYNC_TIMING_ASSERTION,
    sync_timing_policy,
    verify_sync_timing_evidence,
)
from tests.three_site_sync_timing_fixtures import make_sync_timing_artifact


def _signed_campaign(now: datetime):  # noqa: ANN202
    activation = "b" * 40
    enrollment = create_enrollment(
        operator="operator-1",
        password="test matrix approval passphrase",
        now=now,
        scrypt_n=2**14,
    )
    policy = enrollment.policy_payload
    policy_hash = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
    campaign_id = str(uuid4())
    gate_group_id = str(uuid4())
    required_scenarios = scenarios_for_execution_class(SHARED_HOST_SAFE)
    campaign = {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_id": campaign_id,
        "gate_group_id": gate_group_id,
        "execution_class": SHARED_HOST_SAFE,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=48)).isoformat(),
        "baseline_sha": "a" * 40,
        "activation_sha": activation,
        "release_sha": activation,
        "official_staging_url": "https://staging.gold-trade.ir",
        "failover_test_url": "https://app.gold-trading.ir",
        "object_storage": {
            "region": "ir-thr-at1",
            "bucket": "staging-three-site-full-matrix",
            "prefix": f"full-matrix/{gate_group_id}/{SHARED_HOST_SAFE}/{campaign_id}/",
            "versioned": True,
            "private": True,
        },
        "repetitions": 2,
        "required_phases": list(required_scenarios),
        "required_scenarios": {
            phase: list(scenarios) for phase, scenarios in required_scenarios.items()
        },
        "no_skips": True,
        "cleanup_required": True,
        "production_forbidden": True,
        "bound_artifacts": {name: "0" * 64 for name in BOUND_ARTIFACTS},
        "approver_policy_hash": policy_hash,
        "approvals": [],
    }
    return campaign, policy, enrollment


def _sign(campaign: dict, enrollment) -> None:  # noqa: ANN001
    unsigned = {key: value for key, value in campaign.items() if key != "approvals"}
    digest = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    issued_at = datetime.fromisoformat(campaign["generated_at"])
    subject = approval_subject(
        artifact_type=CAMPAIGN_SCHEMA,
        artifact_sha256=digest,
        release_sha=campaign["release_sha"],
        bindings={
            "campaign_id": campaign["campaign_id"],
            "gate_group_id": campaign["gate_group_id"],
            "execution_class": campaign["execution_class"],
        },
    )
    token, _state, _audit = authenticate_and_issue(
        secrets_payload=enrollment.secrets_payload,
        state_payload=enrollment.state_payload,
        policy_payload=enrollment.policy_payload,
        private_key_envelope=enrollment.private_key_envelope,
        password="test matrix approval passphrase",
        totp=totp_code(enrollment.totp_secret, at=issued_at)[1],
        recovery_code=None,
        action="start_full_matrix",
        environment="staging",
        subject=subject,
        ttl_seconds=600,
        now=issued_at,
    )
    campaign["approvals"] = [token]


def _phase_evidence(
    campaign: dict,
    *,
    campaign_hash: str,
    phase: str,
    iteration: int,
    artifact_name: str,
    artifact_hash: str,
    artifact_size: int,
    artifact_root: Path,
) -> dict:
    started = datetime.fromisoformat(campaign["generated_at"]) + timedelta(minutes=iteration)
    scenario_results = []
    artifacts = [
        {"path": artifact_name, "sha256": artifact_hash, "size": artifact_size}
    ]
    for scenario in campaign["required_scenarios"][phase]:
        operation_id = _matrix_operation_id(
            campaign_hash, "scenario", phase=phase,
            scenario_id=scenario, iteration=iteration, attempt=1,
        )
        duration = 86400 if scenario == "twenty_four_hour_endurance_no_growth" else 1
        raw_name = f"raw-{iteration}-{phase}-{scenario}.json"
        raw_payload = canonical_json_bytes({"scenario": scenario, "observed": True}) + b"\n"
        raw_path = artifact_root / raw_name
        raw_path.write_bytes(raw_payload)
        raw_path.chmod(0o600)
        raw_record = {
            "path": raw_name,
            "sha256": hashlib.sha256(raw_payload).hexdigest(),
            "size": len(raw_payload),
        }
        raw_records = [raw_record]
        assertion_names = [
            "operation_executed", "expected_outcome", "production_boundary",
            f"oracle:{scenario}",
        ]
        if duration == 86400:
            assertion_names.append("minimum_duration")
        assertions = []
        for name in assertion_names:
            if name == "minimum_duration":
                expected, observed = 86400, duration
            elif name == "production_boundary":
                expected = observed = False
            elif name == "operation_executed":
                expected = observed = {
                    "operation_id": operation_id,
                    "scenario_id": scenario,
                    "iteration": iteration,
                    "attempt": 1,
                }
            else:
                expected = observed = {"verified": True}
            assertions.append(
                {
                    "name": name,
                    "status": "passed",
                    "expected": expected,
                    "observed": observed,
                    "evidence_refs": [raw_name],
                }
            )
        for assertion_name, contract in customer_actor_pair_contracts(scenario).items():
            pair_name = contract["actor_pair"]
            pair_raw_name = (
                f"raw-customer-{iteration}-{phase}-{scenario}-{pair_name}.json"
            )
            pair_raw_payload = canonical_json_bytes(
                {"scenario": scenario, "customer_contract": contract}
            ) + b"\n"
            pair_raw_path = artifact_root / pair_raw_name
            pair_raw_path.write_bytes(pair_raw_payload)
            pair_raw_path.chmod(0o600)
            pair_raw_record = {
                "path": pair_raw_name,
                "sha256": hashlib.sha256(pair_raw_payload).hexdigest(),
                "size": len(pair_raw_payload),
            }
            raw_records.append(pair_raw_record)
            assertions.append(
                {
                    "name": assertion_name,
                    "status": "passed",
                    "expected": contract,
                    "observed": contract,
                    "evidence_refs": [pair_raw_name],
                }
            )
        if sync_timing_policy(scenario) is not None:
            timing = make_sync_timing_artifact(scenario, captured_at=started)
            timing_name = f"raw-sync-timing-{iteration}-{phase}-{scenario}.json"
            timing_payload = canonical_json_bytes(timing) + b"\n"
            timing_path = artifact_root / timing_name
            timing_path.write_bytes(timing_payload)
            timing_path.chmod(0o600)
            raw_records.append(
                {
                    "path": timing_name,
                    "sha256": hashlib.sha256(timing_payload).hexdigest(),
                    "size": len(timing_payload),
                }
            )
            assertions.append(
                {
                    "name": SYNC_TIMING_ASSERTION,
                    "status": "passed",
                    "expected": sync_timing_policy(scenario),
                    "observed": verify_sync_timing_evidence(
                        timing,
                        scenario_id=scenario,
                    ),
                    "evidence_refs": [timing_name],
                }
            )
        scenario_name = f"scenario-{iteration}-{phase}-{scenario}.json"
        scenario_payload = canonical_json_bytes(
            {
                "schema": SCENARIO_EVIDENCE_SCHEMA,
                "status": "passed",
                "campaign_id": campaign["campaign_id"],
                "campaign_hash": campaign_hash,
                "release_sha": campaign["release_sha"],
                "activation_sha": campaign["activation_sha"],
                "phase": phase,
                "scenario_id": scenario,
                "iteration": iteration,
                "operation_id": operation_id,
                "attempt": 1,
                "oracle_id": f"{phase}.{scenario}.v1",
                "started_at": started.isoformat(),
                "finished_at": (started + timedelta(seconds=duration)).isoformat(),
                "duration_seconds": duration,
                "assertions": assertions,
                "evidence_refs": raw_records,
                "cleanup_residue_count": 0,
                "production_touched": False,
            }
        ) + b"\n"
        scenario_path = artifact_root / scenario_name
        scenario_path.write_bytes(scenario_payload)
        scenario_path.chmod(0o600)
        scenario_record = {
            "path": scenario_name,
            "sha256": hashlib.sha256(scenario_payload).hexdigest(),
            "size": len(scenario_payload),
        }
        artifacts.extend([scenario_record, *raw_records])
        scenario_results.append(
            {
                "scenario_id": scenario,
                "operation_id": operation_id,
                "attempt": 1,
                "status": "passed",
                "assertion_count": len(assertions),
                "evidence_hash": scenario_record["sha256"],
                "duration_seconds": duration,
                "artifact": scenario_record,
            }
        )
    return {
        "schema": PHASE_EVIDENCE_SCHEMA,
        "status": "passed",
        "campaign_id": campaign["campaign_id"],
        "campaign_hash": campaign_hash,
        "release_sha": campaign["release_sha"],
        "activation_sha": campaign["activation_sha"],
        "phase": phase,
        "iteration": iteration,
        "started_at": started.isoformat(),
        "finished_at": (
            started
            + timedelta(
                seconds=86400
                if "twenty_four_hour_endurance_no_growth" in PHASE_SCENARIOS[phase]
                else 1
            )
        ).isoformat(),
        "scenario_results": scenario_results,
        "skip_count": 0,
        "production_touched": False,
        "artifacts": artifacts,
        "cleanup_residue_count": 0 if phase == "cleanup_repeatability" else None,
    }


class ThreeSiteFullMatrixCampaignTests(unittest.TestCase):
    def test_execution_classes_are_disjoint_and_exhaust_the_110_scenario_catalog(self):
        shared = {
            scenario
            for scenarios in scenarios_for_execution_class(SHARED_HOST_SAFE).values()
            for scenario in scenarios
        }
        destructive = {
            scenario
            for scenarios in scenarios_for_execution_class(
                DEDICATED_HOST_DESTRUCTIVE
            ).values()
            for scenario in scenarios
        }
        complete = {scenario for scenarios in PHASE_SCENARIOS.values() for scenario in scenarios}

        self.assertFalse(shared & destructive)
        self.assertEqual(shared | destructive, complete)
        self.assertEqual(len(shared), 104)
        self.assertEqual(len(destructive), 6)
        self.assertEqual(
            destructive,
            {
                "witness_partition_and_vm_pause",
                "fi_host_loss_without_national_cutoff",
                "permanent_fi_recovery_hub_loss",
                "ir_only_active_origin_loss_is_safe_unavailable",
                "power_loss_between_fence_and_enable",
                "wal_event_redis_blob_capacity_exhaustion_safe",
            },
        )

    def test_customer_actor_matrix_is_explicit_in_all_four_lifecycle_states(self):
        placements = {
            "customer_actor_matrix_normal_fi_active": "combined_workload",
            "customer_actor_matrix_iran_active_outage": "partitions_failover",
            "customer_actor_matrix_recovery_ir_routed": "recovery_failback",
            "customer_actor_matrix_post_failback_fi_active": "recovery_failback",
        }
        self.assertEqual(len(CUSTOMER_ACTOR_PAIR_POLICIES), 17)
        self.assertEqual(set(CUSTOMER_LIFECYCLE_MATRIX), set(placements))
        for scenario_id, phase in placements.items():
            with self.subTest(scenario_id=scenario_id):
                self.assertIn(scenario_id, PHASE_SCENARIOS[phase])
                contracts = customer_actor_pair_contracts(scenario_id)
                self.assertEqual(len(contracts), 17)
                self.assertEqual(
                    set(contracts),
                    {
                        customer_actor_pair_assertion_name(actor_pair)
                        for actor_pair in CUSTOMER_ACTOR_PAIR_POLICIES
                    },
                )
        outage_contracts = customer_actor_pair_contracts(
            "customer_actor_matrix_iran_active_outage"
        )
        tier2_request = outage_contracts[
            customer_actor_pair_assertion_name("user__tier2_same_owner")
        ]
        self.assertEqual(
            tier2_request["required_result"],
            "webapp_trade_completed_and_telegram_request_denied",
        )
        tier2_offer = outage_contracts[
            customer_actor_pair_assertion_name("tier2__user_same_owner")
        ]
        self.assertEqual(
            tier2_offer["required_result"],
            "tier2_offer_creation_denied_with_zero_mutation",
        )

    def test_customer_lifecycle_scenario_rejects_missing_forged_or_shared_pair_proof(self):
        now = datetime.now(timezone.utc)
        campaign, _policy, keys = _signed_campaign(now)
        _sign(campaign, keys)
        unsigned = {key: value for key, value in campaign.items() if key != "approvals"}
        campaign_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            phase = "partitions_failover"
            phase_raw = b"phase\n"
            phase_path = root / "phase.json"
            phase_path.write_bytes(phase_raw)
            phase_path.chmod(0o600)
            phase_evidence = _phase_evidence(
                campaign,
                campaign_hash=campaign_hash,
                phase=phase,
                iteration=1,
                artifact_name=phase_path.name,
                artifact_hash=hashlib.sha256(phase_raw).hexdigest(),
                artifact_size=len(phase_raw),
                artifact_root=root,
            )
            scenario_id = "customer_actor_matrix_iran_active_outage"
            result = next(
                item for item in phase_evidence["scenario_results"]
                if item["scenario_id"] == scenario_id
            )
            original = json.loads((root / result["artifact"]["path"]).read_text())
            missing = json.loads(json.dumps(original))
            missing_name = customer_actor_pair_assertion_name("tier1__tier1_other_owner")
            missing["assertions"] = [
                item for item in missing["assertions"] if item["name"] != missing_name
            ]
            with self.assertRaisesRegex(FullMatrixCampaignError, "oracle coverage"):
                verify_scenario_evidence(
                    missing,
                    campaign=campaign,
                    campaign_hash=campaign_hash,
                    phase=phase,
                    scenario_id=scenario_id,
                    iteration=1,
                    attempt=1,
                    operation_id=result["operation_id"],
                    artifact_root=root,
                )

            forged = json.loads(json.dumps(original))
            forged_assertion = next(
                item for item in forged["assertions"]
                if item["name"] == customer_actor_pair_assertion_name(
                    "tier2__user_same_owner"
                )
            )
            forged_assertion["expected"]["required_result"] = (
                "eligible_surface_trade_completed"
            )
            forged_assertion["observed"] = forged_assertion["expected"]
            with self.assertRaisesRegex(FullMatrixCampaignError, "lifecycle proof"):
                verify_scenario_evidence(
                    forged,
                    campaign=campaign,
                    campaign_hash=campaign_hash,
                    phase=phase,
                    scenario_id=scenario_id,
                    iteration=1,
                    attempt=1,
                    operation_id=result["operation_id"],
                    artifact_root=root,
                )

            shared = json.loads(json.dumps(original))
            first_pair = next(
                item for item in shared["assertions"]
                if item["name"] == customer_actor_pair_assertion_name("user__user")
            )
            second_pair = next(
                item for item in shared["assertions"]
                if item["name"] == customer_actor_pair_assertion_name(
                    "user__tier1_same_owner"
                )
            )
            second_pair["evidence_refs"] = first_pair["evidence_refs"]
            with self.assertRaisesRegex(FullMatrixCampaignError, "lifecycle proof"):
                verify_scenario_evidence(
                    shared,
                    campaign=campaign,
                    campaign_hash=campaign_hash,
                    phase=phase,
                    scenario_id=scenario_id,
                    iteration=1,
                    attempt=1,
                    operation_id=result["operation_id"],
                    artifact_root=root,
                )

    def test_timing_scenario_requires_raw_semantic_observation(self):
        now = datetime.now(timezone.utc)
        campaign, _policy, keys = _signed_campaign(now)
        _sign(campaign, keys)
        unsigned = {key: value for key, value in campaign.items() if key != "approvals"}
        campaign_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            raw = b"phase\n"
            phase_path = root / "phase.json"
            phase_path.write_bytes(raw)
            phase_path.chmod(0o600)
            phase = "combined_workload"
            phase_evidence = _phase_evidence(
                campaign,
                campaign_hash=campaign_hash,
                phase=phase,
                iteration=1,
                artifact_name=phase_path.name,
                artifact_hash=hashlib.sha256(raw).hexdigest(),
                artifact_size=len(raw),
                artifact_root=root,
            )
            scenario_id = "three_site_sync_timing_steady_state"
            result = next(
                item for item in phase_evidence["scenario_results"]
                if item["scenario_id"] == scenario_id
            )
            evidence = json.loads((root / result["artifact"]["path"]).read_text())
            missing = json.loads(json.dumps(evidence))
            missing["assertions"] = [
                item for item in missing["assertions"]
                if item["name"] != SYNC_TIMING_ASSERTION
            ]
            with self.assertRaisesRegex(FullMatrixCampaignError, "oracle coverage"):
                verify_scenario_evidence(
                    missing,
                    campaign=campaign,
                    campaign_hash=campaign_hash,
                    phase=phase,
                    scenario_id=scenario_id,
                    iteration=1,
                    attempt=1,
                    operation_id=result["operation_id"],
                    artifact_root=root,
                )

            forged = json.loads(json.dumps(evidence))
            timing_assertion = next(
                item for item in forged["assertions"]
                if item["name"] == SYNC_TIMING_ASSERTION
            )
            timing_assertion["observed"]["sample_count"] += 1
            with self.assertRaisesRegex(FullMatrixCampaignError, "summary is forged"):
                verify_scenario_evidence(
                    forged,
                    campaign=campaign,
                    campaign_hash=campaign_hash,
                    phase=phase,
                    scenario_id=scenario_id,
                    iteration=1,
                    attempt=1,
                    operation_id=result["operation_id"],
                    artifact_root=root,
                )

    def test_required_real_world_failure_catalog_is_explicit(self):
        required = {
            "integer_id_collision_fixtures",
            "natural_identity_cross_site_collision",
            "counter_double_increment_fixture",
            "delete_update_resurrection_fixture",
            "dropped_wakeup_still_durably_drains",
            "table_priority_cannot_overtake_stream_sequence",
            "acknowledged_source_event_absent_target_blocks_promotion",
            "arvan_pop_split_origin_is_safe",
            "certificate_expiry_during_national_outage",
            "dns_global_national_asymmetry",
            "permanent_fi_recovery_hub_loss",
            "ir_only_active_origin_loss_is_safe_unavailable",
            "power_loss_between_fence_and_enable",
            "deployment_or_migration_during_transition_rejected",
            "file_transfer_interruption_resumes_by_hash",
            "startup_mutation_on_fenced_standby_rejected",
            "healthy_link_never_accumulates_backlog",
        }
        actual = {
            scenario
            for scenarios in PHASE_SCENARIOS.values()
            for scenario in scenarios
        }
        self.assertTrue(required <= actual)
        self.assertEqual(len(actual), sum(len(items) for items in PHASE_SCENARIOS.values()))

    def test_legacy_or_tampered_approval_policy_is_rejected(self):
        now = datetime.now(timezone.utc)
        campaign, policy, enrollment = _signed_campaign(now)
        _sign(campaign, enrollment)
        policy["issuer"]["operator"] = "attacker"
        with self.assertRaisesRegex(FullMatrixCampaignError, "human approval"):
            verify_campaign(campaign, approver_policy=policy, now=now)

    def _complete(self):  # noqa: ANN202
        now = datetime.now(timezone.utc)
        campaign, policy, keys = _signed_campaign(now)
        stack = tempfile.TemporaryDirectory()
        root = Path(stack.name)
        root.chmod(0o700)
        bound = {}
        for name in BOUND_ARTIFACTS:
            path = root / f"bound-{name}.json"
            payload = f"bound:{name}".encode()
            path.write_bytes(payload)
            path.chmod(0o600)
            digest = hashlib.sha256(payload).hexdigest()
            campaign["bound_artifacts"][name] = digest
            bound[name] = path
        _sign(campaign, keys)
        unsigned = {key: value for key, value in campaign.items() if key != "approvals"}
        campaign_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
        evidence = []
        for iteration in range(1, campaign["repetitions"] + 1):
            for phase in campaign["required_phases"]:
                artifact_name = f"{iteration}-{phase}.json"
                payload = f"artifact:{iteration}:{phase}".encode()
                path = root / artifact_name
                path.write_bytes(payload)
                path.chmod(0o600)
                evidence.append(
                    _phase_evidence(
                        campaign,
                        campaign_hash=campaign_hash,
                        phase=phase,
                        iteration=iteration,
                        artifact_name=artifact_name,
                        artifact_hash=hashlib.sha256(payload).hexdigest(),
                        artifact_size=len(payload),
                        artifact_root=root,
                    )
                )
        return stack, now, campaign, policy, bound, root, evidence

    def test_complete_two_cycle_no_skips_matrix_passes(self):
        stack, now, campaign, policy, bound, root, evidence = self._complete()
        with stack:
            result = verify_complete_matrix(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                phase_evidence=evidence,
                artifact_root=root,
                now=now + timedelta(minutes=10),
            )
        self.assertEqual(result["status"], "evidence_set_validated")
        self.assertFalse(result["authoritative_controller_journal"])
        self.assertEqual(result["skip_count"], 0)
        self.assertEqual(result["phase_evidence_count"], 2 * len(PHASES))

    def test_retained_evidence_remains_verifiable_after_campaign_expiry(self):
        stack, now, campaign, policy, bound, root, evidence = self._complete()
        with stack:
            result = verify_complete_matrix(
                campaign=campaign,
                approver_policy=policy,
                bound_artifacts=bound,
                phase_evidence=evidence,
                artifact_root=root,
                now=now + timedelta(days=30),
            )
        self.assertEqual(result["status"], "evidence_set_validated")

    def test_missing_phase_or_any_skip_blocks_final_report(self):
        stack, now, campaign, policy, bound, root, evidence = self._complete()
        with stack:
            with self.assertRaisesRegex(FullMatrixCampaignError, "missing"):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=evidence[:-1],
                    artifact_root=root,
                    now=now + timedelta(minutes=10),
                )
            evidence[0]["skip_count"] = 1
            with self.assertRaisesRegex(FullMatrixCampaignError, "identity/status"):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=evidence,
                    artifact_root=root,
                    now=now + timedelta(minutes=10),
                )

    def test_artifact_mutation_or_cross_phase_reuse_is_rejected(self):
        stack, now, campaign, policy, bound, root, evidence = self._complete()
        with stack:
            first_path = root / evidence[0]["artifacts"][0]["path"]
            original_payload = first_path.read_bytes()
            first_path.write_bytes(b"mutated")
            with self.assertRaisesRegex(FullMatrixCampaignError, "hash/size"):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=evidence,
                    artifact_root=root,
                    now=now + timedelta(minutes=10),
                )
            first_path.write_bytes(original_payload)
            first_path.chmod(0o600)
            evidence[1]["artifacts"] = list(evidence[0]["artifacts"])
            with self.assertRaisesRegex(
                FullMatrixCampaignError, "reused|does not retain"
            ):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=evidence,
                    artifact_root=root,
                    now=now + timedelta(minutes=10),
                )

    def test_deleted_raw_scenario_evidence_is_rejected(self):
        stack, now, campaign, policy, bound, root, evidence = self._complete()
        with stack:
            raw = next(
                item for item in evidence[0]["artifacts"] if item["path"].startswith("raw-")
            )
            (root / raw["path"]).unlink()
            with self.assertRaises(FullMatrixCampaignError):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=evidence,
                    artifact_root=root,
                    now=now + timedelta(minutes=10),
                )

    def test_instant_endurance_evidence_is_rejected(self):
        stack, now, campaign, policy, bound, root, evidence = self._complete()
        with stack:
            capacity = next(item for item in evidence if item["phase"] == "capacity_dpi")
            endurance = next(
                item
                for item in capacity["scenario_results"]
                if item["scenario_id"] == "twenty_four_hour_endurance_no_growth"
            )
            path = root / endurance["artifact"]["path"]
            payload = json.loads(path.read_text())
            payload["duration_seconds"] = 1
            payload["finished_at"] = (
                datetime.fromisoformat(payload["started_at"]) + timedelta(seconds=1)
            ).isoformat()
            raw = canonical_json_bytes(payload) + b"\n"
            path.write_bytes(raw)
            path.chmod(0o600)
            digest = hashlib.sha256(raw).hexdigest()
            endurance["artifact"].update(sha256=digest, size=len(raw))
            endurance["evidence_hash"] = digest
            for artifact in capacity["artifacts"]:
                if artifact["path"] == endurance["artifact"]["path"]:
                    artifact.update(sha256=digest, size=len(raw))
            with self.assertRaisesRegex(FullMatrixCampaignError, "under 24 hours"):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=evidence,
                    artifact_root=root,
                    now=now + timedelta(minutes=10),
                )

    def test_world_readable_artifact_root_is_rejected(self):
        stack, now, campaign, policy, bound, root, evidence = self._complete()
        with stack:
            root.chmod(0o755)
            with self.assertRaisesRegex(FullMatrixCampaignError, "owner-only"):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=evidence,
                    artifact_root=root,
                    now=now + timedelta(minutes=10),
                )

    def test_duplicate_phase_evidence_is_rejected(self):
        stack, now, campaign, policy, bound, root, evidence = self._complete()
        with stack:
            evidence.append(dict(evidence[0]))
            with self.assertRaisesRegex(FullMatrixCampaignError, "duplicated"):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=evidence,
                    artifact_root=root,
                    now=now + timedelta(minutes=10),
                )

    def test_campaign_scope_or_signature_drift_is_rejected(self):
        stack, now, campaign, policy, bound, root, evidence = self._complete()
        with stack:
            campaign["official_staging_url"] = "https://app.gold-trading.ir"
            with self.assertRaisesRegex(FullMatrixCampaignError, "scope"):
                verify_complete_matrix(
                    campaign=campaign,
                    approver_policy=policy,
                    bound_artifacts=bound,
                    phase_evidence=evidence,
                    artifact_root=root,
                    now=now + timedelta(minutes=10),
                )


if __name__ == "__main__":
    unittest.main()
