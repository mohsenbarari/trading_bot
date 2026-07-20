from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from core.dr_event_protocol import canonical_json_bytes
from scripts.run_three_site_staging_failover_site_agent import (
    StagingSiteOperationError,
    _readiness_url,
    source_connections_drained,
    source_fenced,
    target_ready,
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
        "readiness_hash": "b" * 64,
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
                return_value={
                    "active_site": "webapp_fi", "writer_epoch": 1,
                    "control_state": "active", "witness_lease_id": "lease",
                    "lease_seconds_remaining": 120,
                },
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
            patch("scripts.run_three_site_staging_failover_site_agent._psql", return_value="1"),
        ):
            with self.assertRaisesRegex(StagingSiteOperationError, "not drained"):
                source_connections_drained(args, _plan(), {})

    def test_target_readiness_is_bound_to_source_tail_and_plan_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = _plan()
            boundary = _boundary(plan)
            tail = Path(directory) / "tail.json"
            tail.write_text(json.dumps({"source_tail_boundary": boundary}))
            tail.chmod(0o600)
            response = {
                "promotion_ready": True,
                "physical_site": "webapp_ir",
                "expected_writer_epoch": 2,
                "readiness_hash": plan.readiness_hash,
                "readiness_evidence": {
                    "evidence_id": "ready",
                    "target_site": "webapp_ir",
                    "writer_epoch": 2,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                },
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
            self.assertEqual(result["readiness_hash"], plan.readiness_hash)
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
