from __future__ import annotations

from datetime import datetime, timezone
import copy
import unittest
import uuid

from core.dr_failover_orchestrator import parse_plan
from core.dr_full_matrix_failover_schedule import build_schedule
from core.human_approval_issuer import create_enrollment
from scripts.build_three_site_full_matrix_failover_plan import (
    FullMatrixFailoverPlanBuildError,
    prepare_plan,
)


class BuildFullMatrixFailoverPlanTests(unittest.TestCase):
    def setUp(self):
        self.campaign = str(uuid.uuid4())
        self.group = str(uuid.uuid4())
        self.release = "a" * 40
        self.schedule = build_schedule(
            campaign_id=self.campaign,
            gate_group_id=self.group,
            execution_class="shared-host-safe",
            release_sha=self.release,
        )
        self.scenario = self.schedule["entries"][0]["scenario_id"]
        self.inventory = {
            "roles": [
                {"role": "webapp_fi", "host_ip": "192.0.2.10"},
                {"role": "webapp_ir", "host_ip": "192.0.2.20"},
            ]
        }
        self.classification = {
            "mode": "isolated",
            "confidence": "high",
            "consecutive_rounds": 3,
            "evidence_hash": "b" * 64,
            "campaign_id": str(uuid.uuid4()),
            "policy_hash": "c" * 64,
        }
        self.policy = create_enrollment(
            operator="operator-1",
            password="correct horse battery staple",
            now=datetime.now(timezone.utc),
            scrypt_n=2**14,
        ).policy_payload

    def test_builds_exact_ten_minute_schedule_bound_draft(self):
        generated = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
        draft, subject, manifest = prepare_plan(
            schedule=self.schedule,
            inventory=self.inventory,
            classification=self.classification,
            policy_payload=self.policy,
            scenario_id=self.scenario,
            iteration=1,
            action="promote_ir",
            expected_epoch=7,
            generated_at=generated,
        )
        parsed = parse_plan(draft, require_approval=False)
        entry = self.schedule["entries"][0]
        self.assertEqual(parsed.operation_id, entry["operation_id"])
        self.assertEqual(parsed.operation_nonce, entry["operation_nonce"])
        self.assertEqual(parsed.expected_epoch, 7)
        self.assertEqual(parsed.target_epoch, 8)
        self.assertEqual(
            (parsed.expires_at - parsed.generated_at).total_seconds(),
            600,
        )
        self.assertEqual(manifest["operation_id"], parsed.operation_id)
        self.assertEqual(subject["artifact_sha256"], parsed.plan_hash)
        self.assertEqual(len(parsed.readiness_commitment), 64)

    def test_rejects_action_classification_and_inventory_drift(self):
        online = copy.deepcopy(self.classification)
        online["mode"] = "online"
        with self.assertRaises(Exception):
            prepare_plan(
                schedule=self.schedule,
                inventory=self.inventory,
                classification=online,
                policy_payload=self.policy,
                scenario_id=self.scenario,
                iteration=1,
                action="promote_ir",
                expected_epoch=7,
                generated_at=datetime.now(timezone.utc),
            )
        incomplete = {"roles": self.inventory["roles"][:1]}
        with self.assertRaises(FullMatrixFailoverPlanBuildError):
            prepare_plan(
                schedule=self.schedule,
                inventory=incomplete,
                classification=self.classification,
                policy_payload=self.policy,
                scenario_id=self.scenario,
                iteration=1,
                action="promote_ir",
                expected_epoch=7,
                generated_at=datetime.now(timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
