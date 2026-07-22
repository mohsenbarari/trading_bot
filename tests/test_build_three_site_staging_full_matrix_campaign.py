from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from core.three_site_full_matrix_campaign import BOUND_ARTIFACTS, FullMatrixCampaignError
from core.human_approval_issuer import authenticate_and_issue, totp_code
from core.three_site_execution_safety import SHARED_HOST_SAFE
from scripts.build_three_site_staging_full_matrix_campaign import (
    _queue_transition,
    finalize,
    prepare,
)
from tests.test_three_site_full_matrix_campaign import _signed_campaign


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


class BuildThreeSiteStagingFullMatrixCampaignTests(unittest.TestCase):
    def _inputs(self):  # noqa: ANN202
        stack = tempfile.TemporaryDirectory()
        root = Path(stack.name)
        root.chmod(0o700)
        baseline = "a" * 40
        activation = "b" * 40
        _unused, policy, enrollment = _signed_campaign(datetime.now(timezone.utc))
        policy_path = root / "policy.json"
        _write(policy_path, policy)
        campaign_id = str(uuid4())
        mappings: dict[str, Path] = {}
        for name in BOUND_ARTIFACTS:
            path = root / f"{name}.json"
            value = {"schema": f"fixture-{name}-v1", "value": name}
            if name == "provisioned_inventory":
                value = {
                    "schema": "three-site-staging-inventory-v3",
                    "inventory_stage": "provisioned",
                    "campaign_id": campaign_id,
                    "release_sha": activation,
                }
            elif name == "queue_activation_transition":
                value = {
                    "schema": "three-site-staging-queue-activation-transition-v1",
                    "status": "verified",
                    "baseline_sha": baseline,
                    "activation_sha": activation,
                    "changed_path": "core/telegram_delivery_runtime_policy.py",
                    "transition_diff_sha256": "c" * 64,
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                }
            _write(path, value)
            mappings[name] = path
        draft = root / "draft.json"
        request = root / "request.json"
        prepare_args = argparse.Namespace(
            bound_artifact=[f"{name}={path}" for name, path in mappings.items()],
            baseline_sha=baseline,
            activation_sha=activation,
            gate_group_id=str(uuid4()),
            execution_class=SHARED_HOST_SAFE,
            approver_policy=policy_path,
            object_bucket="staging-three-site-full-matrix",
            repetitions=2,
            valid_hours=48,
            draft_output=draft,
            approval_request_output=request,
        )
        return (
            stack, root, baseline, activation, policy_path, policy, enrollment,
            mappings, draft, request, prepare_args,
        )

    def test_prepare_then_password_totp_approval_finalizes(self):
        values = self._inputs()
        stack, root, _baseline, activation, policy_path, _policy, enrollment, _mappings, draft, request, args = values
        with stack:
            campaign_id = json.loads(
                values[7]["provisioned_inventory"].read_text()
            )["campaign_id"]
            with patch(
                "scripts.build_three_site_staging_full_matrix_campaign._verify_prerequisites",
                return_value=(campaign_id, "f" * 64, args.object_bucket),
            ):
                prepared = prepare(args)
            self.assertEqual(prepared["status"], "awaiting_password_totp_approval")
            draft_value = json.loads(draft.read_text())
            request_value = json.loads(request.read_text())
            self.assertEqual(draft_value["release_sha"], activation)
            self.assertEqual(request_value["artifact_sha256"], prepared["campaign_hash"])
            issued_at = datetime.now(timezone.utc)
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
                subject=request_value,
                ttl_seconds=600,
                now=issued_at,
            )
            approval = root / "approval.json"
            _write(approval, token)
            output = root / "approved.json"
            finalized = finalize(
                argparse.Namespace(
                    draft=draft,
                    approver_policy=policy_path,
                    approval=approval,
                    output=output,
                )
            )
            self.assertEqual(finalized["status"], "approved")
            self.assertEqual(finalized["campaign_hash"], prepared["campaign_hash"])
            self.assertEqual(len(json.loads(output.read_text())["approvals"]), 1)

    def test_transition_lineage_drift_is_rejected_before_draft(self):
        values = self._inputs()
        stack, _root, _baseline, _activation, _policy_path, _policy, _keys, mappings, draft, _request, args = values
        with stack:
            transition = json.loads(mappings["queue_activation_transition"].read_text())
            transition["baseline_sha"] = "d" * 40
            _write(mappings["queue_activation_transition"], transition)
            with self.assertRaisesRegex(FullMatrixCampaignError, "lineage"):
                _queue_transition(
                    mappings["queue_activation_transition"],
                    baseline_sha=args.baseline_sha,
                    activation_sha=args.activation_sha,
                )
            self.assertFalse(draft.exists())

    def test_bucket_must_match_the_signed_inventory_exactly(self):
        values = self._inputs()
        stack, _root, _baseline, _activation, _policy_path, _policy, _keys, mappings, draft, _request, args = values
        with stack:
            campaign_id = json.loads(
                mappings["provisioned_inventory"].read_text()
            )["campaign_id"]
            with patch(
                "scripts.build_three_site_staging_full_matrix_campaign._verify_prerequisites",
                return_value=(campaign_id, "f" * 64, "gold-trade-staging-three-site-dr"),
            ):
                with self.assertRaisesRegex(FullMatrixCampaignError, "differs"):
                    prepare(args)
            self.assertFalse(draft.exists())

    def test_inventory_must_be_re_attested_at_activation_sha(self):
        values = self._inputs()
        stack, _root, baseline, _activation, _policy_path, _policy, _keys, mappings, draft, _request, args = values
        with stack:
            inventory = json.loads(mappings["provisioned_inventory"].read_text())
            inventory["release_sha"] = baseline
            _write(mappings["provisioned_inventory"], inventory)
            with self.assertRaisesRegex(FullMatrixCampaignError, "re-attested"):
                prepare(args)
            self.assertFalse(draft.exists())

    def test_status_only_or_unsigned_prerequisites_cannot_create_a_draft(self):
        values = self._inputs()
        stack, _root, _baseline, _activation, _policy_path, _policy, _keys, _mappings, draft, _request, args = values
        with stack:
            with self.assertRaises(Exception):
                prepare(args)
            self.assertFalse(draft.exists())


if __name__ == "__main__":
    unittest.main()
