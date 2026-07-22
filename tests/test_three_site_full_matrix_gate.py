from __future__ import annotations

import base64
import argparse
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
from core.three_site_execution_safety import (
    DEDICATED_HOST_DESTRUCTIVE,
    SHARED_HOST_SAFE,
)
from core.three_site_full_matrix_campaign import (
    BOUND_ARTIFACTS,
    POLICY_SCHEMA,
    scenarios_for_execution_class,
    scenario_catalog_sha256,
)
from core.three_site_full_matrix_gate import (
    AGGREGATE_APPROVAL_SCHEMA,
    AGGREGATE_SCHEMA,
    GateDAggregateError,
    verify_component_report,
    verify_gate_d_aggregate,
)
from scripts.build_three_site_staging_gate_d_aggregate import finalize, prepare


RELEASE = "b" * 40


def _component(execution_class: str, gate_group_id: str) -> dict:
    catalog = scenarios_for_execution_class(execution_class)
    scenario_ids = [scenario for scenarios in catalog.values() for scenario in scenarios]
    campaign_id = str(uuid4())
    phase_results = [
        {
            "phase": phase,
            "iteration": iteration,
            "evidence_hash": hashlib.sha256(
                f"{execution_class}:{phase}:{iteration}".encode()
            ).hexdigest(),
            "artifact_count": 1,
            "assertion_count": len(scenarios),
        }
        for iteration in (1, 2)
        for phase, scenarios in catalog.items()
    ]
    body = {
        "schema": "three-site-staging-full-matrix-report-v1",
        "status": "passed",
        "campaign_id": campaign_id,
        "gate_group_id": gate_group_id,
        "execution_class": execution_class,
        "campaign_hash": hashlib.sha256(f"campaign:{campaign_id}".encode()).hexdigest(),
        "release_sha": RELEASE,
        "activation_sha": RELEASE,
        "repetitions": 2,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "authoritative_controller_journal": True,
        "execution_journal": {
            "schema": "three-site-staging-full-matrix-journal-binding-v1",
            "head_before_completion": "1" * 64,
            "finalization_evidence_hash": "2" * 64,
            "scenario_completion_count": 2 * len(scenario_ids),
            "phase_completion_count": len(phase_results),
            "operation_artifacts": [
                {
                    "path": f"{execution_class}-preflight.json",
                    "sha256": "3" * 64,
                    "size": 100,
                    "operation": "preflight:0:::None:0",
                }
            ],
            "operation_artifact_count": 1,
        },
        "bound_artifacts": {
            name: {"sha256": hashlib.sha256(name.encode()).hexdigest(), "size": 1}
            for name in BOUND_ARTIFACTS
        },
        "phase_results": phase_results,
        "phase_evidence_count": len(phase_results),
        "scenario_ids": scenario_ids,
        "scenario_catalog_sha256": scenario_catalog_sha256(execution_class),
        "scenario_execution_count": 2 * len(scenario_ids),
        "skip_count": 0,
        "cleanup_residue_count": 0,
        "production_touched": False,
    }
    return {
        **body,
        "report_hash": hashlib.sha256(canonical_json_bytes(body)).hexdigest(),
    }


def _policy():  # noqa: ANN202
    keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]
    policy = {
        "schema": POLICY_SCHEMA,
        "policy_id": str(uuid4()),
        "release_sha": RELEASE,
        "minimum_approvals": 2,
        "signers": [
            {
                "operator": f"operator-{number}",
                "key_id": f"key-{number}",
                "custody_domain": f"device-{number}",
                "public_key": base64.b64encode(
                    key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
                ).decode(),
            }
            for number, key in enumerate(keys, 1)
        ],
    }
    return policy, keys


def _aggregate(shared: dict, destructive: dict, policy: dict, keys) -> dict:  # noqa: ANN001
    summaries = {}
    for report in (shared, destructive):
        verified = verify_component_report(report)
        summaries[verified["execution_class"]] = verified
    now = datetime.now(timezone.utc)
    policy_hash = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
    value = {
        "schema": AGGREGATE_SCHEMA,
        "gate_group_id": shared["gate_group_id"],
        "release_sha": RELEASE,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "repetitions": 2,
        "component_reports": {
            SHARED_HOST_SAFE: shared,
            DEDICATED_HOST_DESTRUCTIVE: destructive,
        },
        "combined_scenario_count": sum(len(item["scenario_ids"]) for item in summaries.values()),
        "combined_scenario_execution_count": sum(
            item["scenario_execution_count"] for item in summaries.values()
        ),
        "skip_count": 0,
        "cleanup_residue_count": 0,
        "production_touched": False,
        "approver_policy_hash": policy_hash,
        "approvals": [],
    }
    digest = hashlib.sha256(
        canonical_json_bytes({key: item for key, item in value.items() if key != "approvals"})
    ).hexdigest()
    value["approvals"] = [
        {
            "operator": f"operator-{number}",
            "key_id": f"key-{number}",
            "signature": base64.b64encode(key.sign(digest.encode("ascii"))).decode(),
        }
        for number, key in enumerate(keys, 1)
    ]
    return value


class ThreeSiteFullMatrixGateTests(unittest.TestCase):
    def test_builder_binds_both_reports_and_finalizes_dual_approval(self):
        group = str(uuid4())
        shared = _component(SHARED_HOST_SAFE, group)
        destructive = _component(DEDICATED_HOST_DESTRUCTIVE, group)
        policy, keys = _policy()
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)

            def write(name: str, value: dict) -> Path:
                path = root / name
                path.write_text(json.dumps(value, sort_keys=True) + "\n")
                path.chmod(0o600)
                return path

            draft = root / "draft.json"
            request = root / "request.json"
            prepared = prepare(
                argparse.Namespace(
                    shared_report=write("shared.json", shared),
                    destructive_report=write("destructive.json", destructive),
                    approver_policy=write("policy.json", policy),
                    valid_hours=1,
                    draft_output=draft,
                    approval_request_output=request,
                )
            )
            approvals = []
            for number, key in enumerate(keys, 1):
                approvals.append(
                    write(
                        f"approval-{number}.json",
                        {
                            "schema": AGGREGATE_APPROVAL_SCHEMA,
                            "gate_group_id": group,
                            "release_sha": RELEASE,
                            "aggregate_hash": prepared["aggregate_hash"],
                            "operator": f"operator-{number}",
                            "key_id": f"key-{number}",
                            "signature": base64.b64encode(
                                key.sign(prepared["aggregate_hash"].encode("ascii"))
                            ).decode(),
                        },
                    )
                )
            result = finalize(
                argparse.Namespace(
                    draft=draft,
                    approver_policy=root / "policy.json",
                    approval=approvals,
                    output=root / "approved.json",
                )
            )
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["combined_scenario_count"], 110)

    def test_both_disjoint_components_on_same_group_and_sha_pass(self):
        group = str(uuid4())
        shared = _component(SHARED_HOST_SAFE, group)
        destructive = _component(DEDICATED_HOST_DESTRUCTIVE, group)
        policy, keys = _policy()

        result = verify_gate_d_aggregate(
            _aggregate(shared, destructive, policy, keys),
            approver_policy=policy,
        )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["combined_scenario_count"], 110)
        self.assertEqual(result["combined_scenario_execution_count"], 220)

    def test_component_report_tamper_is_rejected(self):
        report = _component(SHARED_HOST_SAFE, str(uuid4()))
        report["production_touched"] = True
        with self.assertRaises(GateDAggregateError):
            verify_component_report(report)

    def test_aggregate_missing_or_cross_group_component_is_rejected(self):
        group = str(uuid4())
        shared = _component(SHARED_HOST_SAFE, group)
        destructive = _component(DEDICATED_HOST_DESTRUCTIVE, str(uuid4()))
        policy, keys = _policy()
        aggregate = _aggregate(shared, destructive, policy, keys)
        with self.assertRaisesRegex(GateDAggregateError, "group/lineage"):
            verify_gate_d_aggregate(aggregate, approver_policy=policy)

        destructive = _component(DEDICATED_HOST_DESTRUCTIVE, group)
        aggregate = _aggregate(shared, destructive, policy, keys)
        del aggregate["component_reports"][DEDICATED_HOST_DESTRUCTIVE]
        with self.assertRaisesRegex(GateDAggregateError, "both execution classes"):
            verify_gate_d_aggregate(aggregate, approver_policy=policy)


if __name__ == "__main__":
    unittest.main()
