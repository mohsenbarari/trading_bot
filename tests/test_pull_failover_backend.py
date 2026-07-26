from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.dr_staging_operation_backend import StagingBackendConfig, StagingHost
from core.writer_witness_auth import WitnessClientCredential
from core.writer_witness_client import WriterWitnessClientConfig
from scripts.full_matrix_live.pull_failover_backend import PullFailoverBackend


def _host(role: str, ip: str) -> StagingHost:
    return StagingHost(
        role=role,
        host_ip=ip,
        ssh_port=22,
        ssh_user="root",
        ssh_identity_file=Path(f"/secure/{role}.key"),
        ssh_known_hosts_file=Path(f"/secure/{role}.known-hosts"),
        repo_root="/srv/trading-bot/current",
        role_compose=f"/etc/trading-bot/{role}.compose.yml",
        env_file=f"/etc/trading-bot/{role}.env",
        plan_path="/secure/plan.json",
        command_manifest_path="/secure/commands.json",
        approver_policy_path="/secure/approvers.json",
        evidence_dir="/secure/evidence",
        recovery_input_path="/secure/recovery.json",
    )


def _config() -> StagingBackendConfig:
    return StagingBackendConfig(
        campaign_id="11111111-1111-4111-8111-111111111111",
        release_sha="a" * 40,
        connectivity_policy=Path("/secure/connectivity-policy.json"),
        connectivity_evidence=Path("/secure/connectivity-evidence.json"),
        arvan_token_file=Path("/secure/arvan-token"),
        arvan_audit_log=Path("/secure/arvan-audit.jsonl"),
        origin_readiness_key_file=Path("/secure/readiness-key"),
        rollback_wait_seconds=60,
        hosts={
            "webapp_fi": _host("webapp_fi", "10.30.0.2"),
            "webapp_ir": _host("webapp_ir", "10.30.0.3"),
        },
        witness_config=WriterWitnessClientConfig(
            base_url="https://witness-dr.staging.internal:8444",
            credential=WitnessClientCredential(
                key_id="staging-webapp-fi-v1",
                site="webapp_fi",
                secret="x" * 32,
            ),
            timeout_seconds=3,
            verify="/secure/ca.crt",
        ),
        witness_public_key="A" * 44,
    )


def _plan(**overrides):  # noqa: ANN202
    values = {
        "operation_id": "22222222-2222-4222-8222-222222222222",
        "plan_hash": "b" * 64,
        "action": "promote_ir",
        "source_site": "webapp_fi",
        "target_site": "webapp_ir",
        "expected_epoch": 1,
        "target_epoch": 2,
        "release_sha": "a" * 40,
        "domain": "gold-trading.ir",
        "record": "app",
        "expected_current_ip": "10.30.0.2",
        "target_ip": "10.30.0.3",
        "classification": {
            "mode": "isolated",
            "confidence": "high",
            "consecutive_rounds": 3,
            "evidence_hash": "c" * 64,
            "campaign_id": "33333333-3333-4333-8333-333333333333",
            "policy_hash": "d" * 64,
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class PullFailoverBackendTests(unittest.TestCase):
    def test_jit_plan_and_manifest_are_staged_only_to_wa_fi(self):
        plan = _plan()
        backend = PullFailoverBackend(
            _config(),
            plan_document={"operation_id": plan.operation_id},
            pull_operation=lambda *_args: {"status": "ok", "operation_id": plan.operation_id},
        )
        calls = []

        def copied(argv, **_kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        with (
            patch(
                "scripts.full_matrix_live.pull_failover_backend.parse_plan",
                return_value=plan,
            ),
            patch(
                "scripts.full_matrix_live.pull_failover_backend.subprocess.run",
                side_effect=copied,
            ),
        ):
            backend.materialize_webapp_fi_inputs(plan)
        self.assertEqual(len(calls), 2)
        destinations = [argv[-1] for argv in calls]
        self.assertTrue(all("10.30.0.2" in destination for destination in destinations))
        self.assertTrue(all("10.30.0.3" not in destination for destination in destinations))
        self.assertTrue(any(destination.endswith(":/secure/plan.json") for destination in destinations))
        self.assertTrue(any(destination.endswith(":/secure/commands.json") for destination in destinations))

    def test_wa_ir_target_never_uses_direct_compose_or_ssh(self):
        calls = []

        def pull(plan, document, action, boundary, readiness, previous):
            calls.append((plan.operation_id, document, action, boundary, readiness, previous))
            return {"status": "ok", "operation_id": plan.operation_id}

        backend = PullFailoverBackend(
            _config(),
            plan_document={"approved": "plan"},
            pull_operation=pull,
        )
        plan = _plan()
        boundary = {"boundary": "proof"}
        readiness = {"target-ready": "evidence"}

        async def exercise():
            target_ready = await backend.target_ready(
                plan,
                source_tail_boundary=boundary,
            )
            acquired = await backend.target_term_acquired_with_readiness(
                plan,
                target_readiness=readiness,
            )
            return target_ready, acquired

        async def immediate(function, *args, **kwargs):  # noqa: ANN001
            return function(*args, **kwargs)

        from unittest.mock import patch

        with patch(
            "scripts.full_matrix_live.pull_failover_backend.asyncio.to_thread",
            new=immediate,
        ), patch(
            "core.dr_staging_operation_backend.asyncio.to_thread",
            new=immediate,
        ):
            ready, acquired = asyncio.run(exercise())
        self.assertEqual(ready["status"], "ok")
        self.assertEqual(acquired["status"], "ok")
        self.assertEqual([item[2] for item in calls], ["target-ready", "target-term-acquired"])
        self.assertEqual(calls[0][3], boundary)
        self.assertEqual(calls[1][4], readiness)

    def test_wa_ir_source_uses_atomic_drain_and_fence_pull_operation(self):
        calls = []

        def pull(plan, document, action, boundary, readiness, previous):
            del document, boundary, readiness, previous
            calls.append(action)
            return {"status": "ok", "operation_id": plan.operation_id}

        plan = _plan(
            action="failback_fi",
            source_site="webapp_ir",
            target_site="webapp_fi",
            expected_epoch=2,
            target_epoch=3,
            expected_current_ip="10.30.0.3",
            target_ip="10.30.0.2",
            classification={
                "mode": "online",
                "confidence": "high",
                "consecutive_rounds": 3,
                "evidence_hash": "c" * 64,
                "campaign_id": "33333333-3333-4333-8333-333333333333",
                "policy_hash": "d" * 64,
            },
        )
        backend = PullFailoverBackend(
            _config(), plan_document={"approved": "plan"}, pull_operation=pull
        )
        async def immediate(function, *args, **kwargs):  # noqa: ANN001
            return function(*args, **kwargs)

        from unittest.mock import patch

        with patch(
            "scripts.full_matrix_live.pull_failover_backend.asyncio.to_thread",
            new=immediate,
        ), patch(
            "core.dr_staging_operation_backend.asyncio.to_thread",
            new=immediate,
        ):
            result = asyncio.run(backend.source_fenced(plan))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(calls, ["source-drained-and-fenced"])


if __name__ == "__main__":
    unittest.main()
