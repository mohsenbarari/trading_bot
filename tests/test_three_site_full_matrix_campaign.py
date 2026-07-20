from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.dr_event_protocol import canonical_json_bytes
from core.three_site_full_matrix_campaign import (
    BOUND_ARTIFACTS,
    CAMPAIGN_SCHEMA,
    PHASES,
    PHASE_EVIDENCE_SCHEMA,
    PHASE_SCENARIOS,
    POLICY_SCHEMA,
    FullMatrixCampaignError,
    verify_campaign,
    verify_complete_matrix,
)


def _signed_campaign(now: datetime):  # noqa: ANN202
    private_keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]
    activation = "b" * 40
    policy = {
        "schema": POLICY_SCHEMA,
        "policy_id": str(uuid4()),
        "release_sha": activation,
        "minimum_approvals": 2,
        "signers": [
            {
                "operator": f"operator-{number}",
                "key_id": f"matrix-key-{number}",
                "custody_domain": f"device-{number}",
                "public_key": base64.b64encode(
                    private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
                ).decode(),
            }
            for number, private in enumerate(private_keys, 1)
        ],
    }
    policy_hash = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
    campaign_id = str(uuid4())
    campaign = {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_id": campaign_id,
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
            "prefix": f"full-matrix/{campaign_id}/",
            "versioned": True,
            "private": True,
        },
        "repetitions": 2,
        "required_phases": list(PHASES),
        "required_scenarios": {
            phase: list(scenarios) for phase, scenarios in PHASE_SCENARIOS.items()
        },
        "no_skips": True,
        "cleanup_required": True,
        "production_forbidden": True,
        "bound_artifacts": {name: "0" * 64 for name in BOUND_ARTIFACTS},
        "approver_policy_hash": policy_hash,
        "approvals": [],
    }
    return campaign, policy, private_keys


def _sign(campaign: dict, private_keys: list[Ed25519PrivateKey]) -> None:
    unsigned = {key: value for key, value in campaign.items() if key != "approvals"}
    digest = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    campaign["approvals"] = [
        {
            "operator": f"operator-{number}",
            "key_id": f"matrix-key-{number}",
            "signature": base64.b64encode(private.sign(digest.encode("ascii"))).decode(),
        }
        for number, private in enumerate(private_keys, 1)
    ]


def _phase_evidence(
    campaign: dict,
    *,
    campaign_hash: str,
    phase: str,
    iteration: int,
    artifact_name: str,
    artifact_hash: str,
    artifact_size: int,
) -> dict:
    started = datetime.fromisoformat(campaign["generated_at"]) + timedelta(minutes=iteration)
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
        "finished_at": (started + timedelta(seconds=1)).isoformat(),
        "scenario_results": [
            {
                "scenario_id": scenario,
                "status": "passed",
                "assertion_count": 1,
                "evidence_hash": hashlib.sha256(
                    f"{phase}:{iteration}:{scenario}".encode()
                ).hexdigest(),
            }
            for scenario in PHASE_SCENARIOS[phase]
        ],
        "skip_count": 0,
        "production_touched": False,
        "artifacts": [
            {
                "path": artifact_name,
                "sha256": artifact_hash,
                "size": artifact_size,
            }
        ],
        "cleanup_residue_count": 0 if phase == "cleanup_repeatability" else None,
    }


class ThreeSiteFullMatrixCampaignTests(unittest.TestCase):
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

    def test_two_signer_policy_cannot_reuse_one_public_key(self):
        now = datetime.now(timezone.utc)
        campaign, policy, keys = _signed_campaign(now)
        policy["signers"][1]["public_key"] = policy["signers"][0]["public_key"]
        campaign["approver_policy_hash"] = hashlib.sha256(
            canonical_json_bytes(policy)
        ).hexdigest()
        _sign(campaign, [keys[0], keys[0]])
        with self.assertRaisesRegex(FullMatrixCampaignError, "independent"):
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
            for phase in PHASES:
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
            with self.assertRaisesRegex(FullMatrixCampaignError, "reused"):
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
