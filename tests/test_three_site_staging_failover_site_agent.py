from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from core.dr_event_protocol import canonical_json_bytes
from core.webapp_writer_control import validate_readiness_evidence
from scripts.run_three_site_staging_failover_site_agent import (
    StagingSiteOperationError,
    _common,
    _readiness_url,
    source_connections_drained,
    source_drained_and_fenced,
    source_fenced,
    target_ready,
    target_term_acquired,
    target_term_attested,
)


def _plan(**overrides):
    values = {
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "source_site": "webapp_fi",
        "target_site": "webapp_ir",
        "expected_epoch": 1,
        "target_epoch": 2,
        "action": "promote_ir",
        "release_sha": "a" * 40,
        "readiness_commitment": "b" * 64,
        "rpo_policy": {
            "mode": "zero_loss", "max_unreplicated_events": 0,
            "approval_reason": None, "approval_ticket": None,
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _boundary(plan) -> dict:  # noqa: ANN001
    unsigned = {
        "mode": "proven",
        "origin_site": plan.source_site,
        "target_site": plan.target_site,
        "producer_epoch": plan.expected_epoch,
        "final_sequence": 7,
        "final_transaction_hash": "c" * 64,
        "estimated_unreplicated_events": 0,
    }
    import hashlib

    return {
        **unsigned,
        "boundary_hash": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
    }


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return json.dumps(self.payload).encode()


class ThreeSiteStagingFailoverSiteAgentTests(unittest.TestCase):
    def test_common_uses_release_bound_witness_key_for_relay_receipt(self):
        args = SimpleNamespace(
            action="target-ready",
            role="webapp_ir",
            plan=Path("/secure/plan.json"),
            approver_policy=Path("/secure/policy.json"),
            command_manifest=Path("/secure/manifest.json"),
            role_compose=Path("/secure/compose.yml"),
            env_file=Path("/secure/role.env"),
        )
        plan = _plan()
        env = (
            "STAGING_RELEASE_SHA=" + plan.release_sha + "\n"
            "PHYSICAL_SITE=webapp_ir\n"
            "WRITER_WITNESS_PUBLIC_KEY=release-bound-witness-public-key\n"
        ).encode()
        with (
            patch(
                "scripts.run_three_site_staging_failover_site_agent._strict_json",
                return_value={},
            ),
            patch(
                "scripts.run_three_site_staging_failover_site_agent.parse_plan",
                return_value=plan,
            ),
            patch(
                "scripts.run_three_site_staging_failover_site_agent.load_approver_policy",
                return_value={},
            ),
            patch(
                "scripts.run_three_site_staging_failover_site_agent._verify_bundle_source",
                side_effect=[b"compose", env],
            ),
            patch(
                "scripts.run_three_site_staging_failover_site_agent.verify_human_failover_approval",
            ) as verify,
            patch(
                "scripts.run_three_site_staging_failover_site_agent.validate_plan_freshness",
            ),
            patch(
                "scripts.run_three_site_staging_failover_site_agent.load_typed_operation_manifest",
            ),
        ):
            result, parsed_env, _compose_hash, _env_hash = _common(args)
        self.assertIs(result, plan)
        self.assertEqual(parsed_env["PHYSICAL_SITE"], "webapp_ir")
        self.assertEqual(
            verify.call_args.kwargs["witness_relay_public_key"],
            "release-bound-witness-public-key",
        )

    def test_source_fence_stops_all_public_mutators_before_tail_capture(self):
        args = SimpleNamespace(role="webapp_fi")
        plan = _plan()
        calls = []
        queries = []

        def run(arguments, **_kwargs):
            calls.append(arguments)
            return ""

        def psql(_args, _env, sql):
            queries.append(sql)
            if "pg_stat_activity" in sql:
                return "0"
            if "pg_terminate_backend" in sql:
                return "0"
            return "7|" + "c" * 64

        with (
            patch(
                "scripts.run_three_site_staging_failover_site_agent._writer_state",
                side_effect=[{
                    "active_site": "webapp_fi", "writer_epoch": 1,
                    "control_state": "active", "witness_lease_id": "lease",
                    "lease_seconds_remaining": 120,
                }, {
                    "active_site": None, "writer_epoch": 1,
                    "control_state": "fenced", "witness_lease_id": None,
                    "lease_seconds_remaining": None,
                }],
            ),
            patch(
                "scripts.run_three_site_staging_failover_site_agent._compose",
                return_value=["docker", "compose"],
            ),
            patch("scripts.run_three_site_staging_failover_site_agent._run", side_effect=run),
            patch(
                "scripts.run_three_site_staging_failover_site_agent._psql",
                side_effect=psql,
            ),
        ):
            result = source_fenced(args, plan, {})
        self.assertTrue(result["fenced"])
        self.assertEqual(result["source_tail_boundary"]["final_sequence"], 7)
        self.assertIn("webapp_fi_api", calls[0])
        self.assertIn("webapp_fi_effects", calls[0])
        self.assertTrue(result["boundary_captured_after_drain"])
        self.assertTrue(result["admission_fence"])
        self.assertEqual(result["control_state"], "fenced")
        self.assertEqual(result["active_connections"], 0)
        self.assertTrue(
            any("destination_streams -> 'webapp_ir'" in query for query in queries)
        )

    def test_source_drain_fails_if_application_connection_remains(self):
        args = SimpleNamespace(role="webapp_fi")
        with (
            patch(
                "scripts.run_three_site_staging_failover_site_agent._compose",
                return_value=["docker", "compose"],
            ),
            patch("scripts.run_three_site_staging_failover_site_agent._run", return_value=""),
            patch(
                "scripts.run_three_site_staging_failover_site_agent._writer_state",
                return_value={
                    "active_site": None, "writer_epoch": 1,
                    "control_state": "fenced",
                },
            ),
            patch("scripts.run_three_site_staging_failover_site_agent._psql", return_value="1"),
        ):
            with self.assertRaisesRegex(StagingSiteOperationError, "not drained"):
                source_connections_drained(args, _plan(), {})

    def test_ir_source_drain_and_fence_is_one_local_closed_operation(self):
        plan = _plan(
            action="failback_fi",
            source_site="webapp_ir",
            target_site="webapp_fi",
            expected_epoch=2,
            target_epoch=3,
        )
        args = SimpleNamespace(role="webapp_ir")
        calls = []

        def run(arguments, **_kwargs):
            calls.append(arguments)
            if any("drain_three_site_staging_writer_lease.py" in value for value in arguments):
                request_id = arguments[arguments.index("--request-id") + 1]
                return json.dumps(
                    {
                        "status": "draining",
                        "operation_id": plan.operation_id,
                        "request_id": request_id,
                        "source_site": "webapp_ir",
                        "writer_epoch": 2,
                        "witness_receipt_hash": "d" * 64,
                    }
                )
            return ""

        with (
            patch(
                "scripts.run_three_site_staging_failover_site_agent._compose",
                return_value=["docker", "compose"],
            ),
            patch("scripts.run_three_site_staging_failover_site_agent._run", side_effect=run),
            patch(
                "scripts.run_three_site_staging_failover_site_agent.source_fenced",
                return_value={
                    "status": "ok",
                    "operation_id": plan.operation_id,
                    "source_site": "webapp_ir",
                    "fenced": True,
                    "active_connections": 0,
                    "boundary_captured_after_drain": True,
                    "admission_fence": True,
                    "control_state": "fenced",
                    "source_tail_boundary": _boundary(plan),
                    "evidence_hash": "e" * 64,
                },
            ),
        ):
            result = source_drained_and_fenced(args, plan, {})
        self.assertEqual(result["witness_drain_receipt_hash"], "d" * 64)
        self.assertTrue(
            any(
                any("drain_three_site_staging_writer_lease.py" in value for value in call)
                for call in calls
            )
        )

    def test_target_readiness_is_bound_to_source_tail_and_plan_commitment(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = _plan()
            boundary = _boundary(plan)
            tail = Path(directory) / "tail.json"
            tail.write_text(json.dumps({"source_tail_boundary": boundary}))
            tail.chmod(0o600)
            now = datetime.now(timezone.utc)
            evidence = {
                "evidence_id": "ready",
                "target_site": "webapp_ir",
                "writer_epoch": 2,
                "action": "promote_ir",
                "generated_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=30)).isoformat(),
                "schema_compatible": True,
                "release_compatible": True,
                "database_ready": True,
                "storage_ready": True,
                "sync_checkpoint_ready": True,
                "no_critical_conflicts": True,
                "background_jobs_ready": True,
                "fencing_acknowledged": True,
                "source_tail_boundary_hash": boundary["boundary_hash"],
                "target_applied_sequence": 7,
                "target_applied_through_boundary": True,
                "readiness_commitment": plan.readiness_commitment,
            }
            evidence_hash = validate_readiness_evidence(
                evidence,
                target_site="webapp_ir",
                writer_epoch=2,
                now=now,
                max_age_seconds=60,
            ).content_hash
            response = {
                "promotion_ready": True,
                "physical_site": "webapp_ir",
                "expected_writer_epoch": 2,
                "readiness_hash": evidence_hash,
                "readiness_commitment": plan.readiness_commitment,
                "readiness_evidence": evidence,
                "source_tail_boundary": boundary,
                "target_applied_sequence": 7,
            }
            opener = SimpleNamespace(open=lambda *_args, **_kwargs: _Response(response))
            args = SimpleNamespace(
                role="webapp_ir", source_tail=tail, source_tail_json=None,
                recovery_input=None,
            )
            with patch(
                "scripts.run_three_site_staging_failover_site_agent.urllib.request.build_opener",
                return_value=opener,
            ):
                result = target_ready(
                    args, plan, {"ORIGIN_READINESS_API_KEY": "private-key"}
                )
            self.assertEqual(result["readiness_commitment"], plan.readiness_commitment)
            self.assertEqual(result["readiness_evidence_hash"], evidence_hash)
            self.assertEqual(result["source_tail_boundary_hash"], boundary["boundary_hash"])

    def test_failback_url_requires_recovery_manifest_values(self):
        plan = _plan(
            action="failback_fi",
            source_site="webapp_ir",
            target_site="webapp_fi",
            expected_epoch=2,
            target_epoch=3,
        )
        with self.assertRaisesRegex(StagingSiteOperationError, "recovery-manifest"):
            _readiness_url(plan, _boundary(plan), None)

    def test_target_term_acquisition_is_local_and_binds_readiness_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = _plan()
            now = datetime.now(timezone.utc)
            evidence = {
                "evidence_id": "ready",
                "target_site": "webapp_ir",
                "writer_epoch": 2,
                "action": "promote_ir",
                "generated_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=30)).isoformat(),
                "schema_compatible": True,
                "release_compatible": True,
                "database_ready": True,
                "storage_ready": True,
                "sync_checkpoint_ready": True,
                "no_critical_conflicts": True,
                "background_jobs_ready": True,
                "fencing_acknowledged": True,
                "source_tail_boundary_hash": "c" * 64,
                "target_applied_sequence": 7,
                "target_applied_through_boundary": True,
                "readiness_commitment": plan.readiness_commitment,
            }
            evidence_hash = validate_readiness_evidence(
                evidence,
                target_site="webapp_ir",
                writer_epoch=2,
                now=now,
                max_age_seconds=60,
            ).content_hash
            readiness = Path(directory) / "readiness.json"
            readiness.write_text(
                json.dumps(
                    {
                        "schema": "three-site-staging-target-readiness-v1",
                        "status": "ok",
                        "operation_id": plan.operation_id,
                        "release_sha": plan.release_sha,
                        "target_site": "webapp_ir",
                        "target_epoch": 2,
                        "readiness_commitment": plan.readiness_commitment,
                        "readiness_evidence_hash": evidence_hash,
                        "readiness_evidence": evidence,
                        "evidence_hash": "d" * 64,
                    }
                ),
                encoding="utf-8",
            )
            readiness.chmod(0o600)
            args = SimpleNamespace(
                role="webapp_ir",
                readiness_evidence=readiness,
                previous_proof_hash=None,
            )
            calls = []

            def run(arguments, **_kwargs):
                calls.append(arguments)
                if any(
                    "activate_three_site_staging_failover_target.py" in value
                    for value in arguments
                ):
                    return json.dumps(
                        {
                            "status": "ok",
                            "operation_id": plan.operation_id,
                            "writer_epoch": 2,
                            "proof_hash": "e" * 64,
                        }
                    )
                return ""

            with (
                patch(
                    "scripts.run_three_site_staging_failover_site_agent._inspect_witness",
                    return_value={"lease_live": False},
                ),
                patch(
                    "scripts.run_three_site_staging_failover_site_agent._compose",
                    return_value=["docker", "compose"],
                ),
                patch("scripts.run_three_site_staging_failover_site_agent._run", side_effect=run),
                patch(
                    "scripts.run_three_site_staging_failover_site_agent.target_term_attested",
                    return_value={
                        "holder_site": "webapp_ir",
                        "writer_epoch": 2,
                        "lease_id": "lease",
                        "proof_hash": "f" * 64,
                        "lease_seconds_remaining": 120,
                        "evidence_hash": "a" * 64,
                    },
                ),
            ):
                result = target_term_acquired(args, plan, {})
        self.assertEqual(result["writer_epoch"], 2)
        self.assertEqual(result["acquisition_proof_hash"], "e" * 64)
        activation = next(
            call for call in calls
            if any("activate_three_site_staging_failover_target.py" in value for value in call)
        )
        self.assertIn(f"{readiness}:/run/failover/readiness.json:ro", activation)

    def test_target_term_requires_control_agent_post_acquisition_renewal(self):
        previous = "d" * 64
        current = "e" * 64
        args = SimpleNamespace(
            role="webapp_ir", previous_proof_hash=previous,
        )

        def run(arguments, **_kwargs):
            if "inspect" in arguments:
                return "true"
            return "writer-control-container"

        with (
            patch(
                "scripts.run_three_site_staging_failover_site_agent._compose",
                return_value=["docker", "compose"],
            ),
            patch(
                "scripts.run_three_site_staging_failover_site_agent._run",
                side_effect=run,
            ),
            patch(
                "scripts.run_three_site_staging_failover_site_agent._writer_state",
                return_value={
                    "active_site": "webapp_ir",
                    "writer_epoch": 2,
                    "control_state": "active",
                    "witness_lease_id": "lease-2",
                    "witness_proof_hash": current,
                    "lease_seconds_remaining": 120,
                },
            ),
        ):
            result = target_term_attested(args, _plan(), {})
        self.assertTrue(result["control_agent_running"])
        self.assertEqual(result["proof_hash"], current)
        self.assertNotEqual(result["proof_hash"], previous)


if __name__ == "__main__":
    unittest.main()
