from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from core.dr_staging_operation_backend import (
    StagingBackendConfig,
    StagingHost,
    StagingOperationBackendError,
    StagingTypedOperationBackend,
    load_staging_backend_config,
)
from core.writer_witness_auth import WitnessClientCredential
from core.writer_witness_client import WriterWitnessClientConfig
from tests.test_three_site_staging_signed_inventory import _inventory, _signed_documents


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


class StagingOperationBackendTests(unittest.TestCase):
    def test_preflight_binds_fresh_classification_and_local_credentials(self):
        backend = StagingTypedOperationBackend(_config())
        plan = _plan()
        observed = SimpleNamespace(**plan.classification)
        with (
            patch(
                "core.dr_staging_operation_backend.load_connectivity_policy",
                return_value=object(),
            ),
            patch(
                "core.dr_staging_operation_backend.load_rounds",
                return_value=[],
            ),
            patch(
                "core.dr_staging_operation_backend.classify_connectivity",
                return_value=observed,
            ),
            patch(
                "core.dr_staging_operation_backend.load_token",
                return_value="provider-token",
            ),
            patch(
                "core.dr_staging_operation_backend.read_secure_text",
                return_value="r" * 32,
            ),
        ):
            backend.preflight(plan)

        drifted = SimpleNamespace(**{**plan.classification, "mode": "online"})
        with (
            patch(
                "core.dr_staging_operation_backend.load_connectivity_policy",
                return_value=object(),
            ),
            patch("core.dr_staging_operation_backend.load_rounds", return_value=[]),
            patch(
                "core.dr_staging_operation_backend.classify_connectivity",
                return_value=drifted,
            ),
        ):
            with self.assertRaisesRegex(
                StagingOperationBackendError, "fresh connectivity"
            ):
                backend.preflight(plan)

    def test_secure_config_is_bound_to_fresh_signed_provisioned_inventory(self):
        inventory = _inventory()
        policy, approval = _signed_documents(inventory, datetime.now(timezone.utc))
        role_ips = {item["role"]: item["host_ip"] for item in inventory["roles"]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = {}
            for name, content in {
                "fi-key": "private-key",
                "fi-hosts": "host-key",
                "ir-key": "private-key",
                "ir-hosts": "host-key",
                "witness-secret": "w" * 32,
                "ca": "test-ca",
            }.items():
                path = root / name
                path.write_text(content, encoding="utf-8")
                path.chmod(0o600)
                protected[name] = str(path)
            host_template = {
                "ssh_port": 22,
                "ssh_user": "root",
                "repo_root": "/srv/trading-bot/current",
                "role_compose": "/etc/trading-bot/role.compose.yml",
                "env_file": "/etc/trading-bot/role.env",
                "plan_path": "/secure/plan.json",
                "command_manifest_path": "/secure/commands.json",
                "approver_policy_path": "/secure/approvers.json",
                "evidence_dir": "/secure/evidence",
                "recovery_input_path": "/secure/recovery.json",
            }
            payload = {
                "schema": "three-site-staging-failover-backend-v1",
                "campaign_id": inventory["campaign_id"],
                "release_sha": inventory["release_sha"],
                "domain": "gold-trading.ir",
                "record": "app",
                "connectivity_policy": "/secure/connectivity-policy.json",
                "connectivity_evidence": "/secure/connectivity-evidence.json",
                "arvan_token_file": "/secure/arvan-token",
                "arvan_audit_log": "/secure/arvan-audit.jsonl",
                "origin_readiness_key_file": "/secure/readiness-key",
                "rollback_wait_seconds": 60,
                "hosts": {
                    "webapp_fi": {
                        **host_template,
                        "host_ip": role_ips["webapp_fi"],
                        "ssh_identity_file": protected["fi-key"],
                        "ssh_known_hosts_file": protected["fi-hosts"],
                    },
                    "webapp_ir": {
                        **host_template,
                        "host_ip": role_ips["webapp_ir"],
                        "ssh_identity_file": protected["ir-key"],
                        "ssh_known_hosts_file": protected["ir-hosts"],
                    },
                },
                "witness": {
                    "base_url": "https://witness-dr.staging.internal:8444",
                    "key_id": "staging-webapp-fi-v1",
                    "site": "webapp_fi",
                    "secret_file": protected["witness-secret"],
                    "ca_bundle": protected["ca"],
                    "public_key": base64.b64encode(b"p" * 32).decode(),
                    "timeout_seconds": 3,
                },
            }
            path = root / "backend.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)
            config = load_staging_backend_config(
                path,
                inventory=inventory,
                inventory_approval=approval,
                inventory_approval_policy=policy,
            )
            self.assertEqual(config.hosts["webapp_ir"].host_ip, role_ips["webapp_ir"])
            payload["hosts"]["webapp_ir"]["host_ip"] = "10.30.0.99"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(StagingOperationBackendError, "host identity"):
                load_staging_backend_config(
                    path,
                    inventory=inventory,
                    inventory_approval=approval,
                    inventory_approval_policy=policy,
                )

    def test_plan_scope_is_bound_to_signed_role_addresses(self):
        backend = StagingTypedOperationBackend(_config())
        backend.validate_plan_scope(_plan())
        with self.assertRaisesRegex(StagingOperationBackendError, "signed staging topology"):
            backend.validate_plan_scope(_plan(target_ip="10.30.0.99"))

    def test_compose_lifecycle_accepts_normal_non_json_output(self):
        backend = StagingTypedOperationBackend(_config())
        with patch.object(
            backend,
            "_ssh_raw",
            return_value=subprocess.CompletedProcess([], 0, "", "service stopped"),
        ) as execute:
            backend._compose_service(
                backend.config.hosts["webapp_fi"],
                "stop",
                ["webapp_fi_writer_control"],
            )
        command = execute.call_args.args[1]
        self.assertEqual(command[0:2], ["/usr/bin/docker", "compose"])
        self.assertIn("stop", command)

    def test_json_agent_rejects_ambiguous_extra_output(self):
        backend = StagingTypedOperationBackend(_config())
        completed = subprocess.CompletedProcess(
            [], 0, '{"status":"ok"}\nunexpected\n', ""
        )
        with patch.object(backend, "_ssh_raw", return_value=completed):
            with self.assertRaisesRegex(StagingOperationBackendError, "ambiguous"):
                backend._ssh(backend.config.hosts["webapp_fi"], ["fixed-command"])

    def test_readiness_path_survives_controller_restart(self):
        plan = _plan()
        first = StagingTypedOperationBackend(_config())
        restarted = StagingTypedOperationBackend(_config())
        host = first.config.hosts["webapp_ir"]
        self.assertEqual(
            first._site_evidence_path(host, plan, "target-ready"),
            restarted._site_evidence_path(host, plan, "target-ready"),
        )

    def test_target_term_waits_for_a_fresh_control_agent_renewal(self):
        backend = StagingTypedOperationBackend(_config())
        plan = _plan()
        acquisition = {
            "status": "activated",
            "target_site": "webapp_ir",
            "writer_epoch": 2,
            "lease_id": "lease-acquired",
            "proof_hash": "d" * 64,
        }
        renewal = {
            "status": "ok",
            "holder_site": "webapp_ir",
            "writer_epoch": 2,
            "lease_id": "lease-acquired",
            "proof_hash": "e" * 64,
            "lease_seconds_remaining": 120,
            "evidence_hash": "f" * 64,
        }

        async def exercise():
            async def immediate(function, *args, **kwargs):  # noqa: ANN001
                return function(*args, **kwargs)

            with (
                patch(
                    "core.dr_staging_operation_backend.asyncio.to_thread",
                    new=immediate,
                ),
                patch(
                    "core.dr_staging_operation_backend.validate_plan_freshness"
                ),
                patch.object(backend, "_drain_source_lease", new=lambda *_args: {}),
                patch.object(
                    backend,
                    "_inspect_witness",
                    new=lambda *_args, **_kwargs: {"lease_live": False},
                ),
                patch.object(
                    backend,
                    "_compose_run",
                    new=lambda *_args, **_kwargs: acquisition,
                ),
                patch.object(backend, "_compose_service", new=lambda *_args: None),
                patch.object(
                    backend,
                    "_site_agent",
                    return_value=renewal,
                ) as site_agent,
            ):
                result = await backend.target_term_acquired(plan)
                return result, site_agent

        result, site_agent = asyncio.run(exercise())
        self.assertEqual(result["proof_hash"], "e" * 64)
        self.assertEqual(result["acquisition_proof_hash"], "d" * 64)
        self.assertEqual(
            site_agent.call_args.kwargs["previous_proof_hash"], "d" * 64
        )

    def test_rollback_reports_both_sites_fenced_and_connection_free(self):
        backend = StagingTypedOperationBackend(_config())
        plan = _plan()
        async def exercise():
            async def immediate(function, *args, **kwargs):  # noqa: ANN001
                return function(*args, **kwargs)

            with (
                patch(
                    "core.dr_staging_operation_backend.asyncio.to_thread",
                    new=immediate,
                ),
                patch.object(
                    backend,
                    "_site_agent",
                    new=lambda *_args, role, **_kwargs: {
                        "status": "ok", "role": role, "active_connections": 0
                    },
                ),
                patch.object(
                    backend,
                    "_inspect_witness",
                    new=lambda *_args, **_kwargs: {
                        "lease_live": False,
                        "holder_site": "webapp_fi",
                        "witness_receipt_hash": "c" * 64,
                        "request_id": "witness-status-1",
                    },
                ),
                patch(
                    "core.dr_staging_operation_backend.load_token",
                    new=lambda *_args, **_kwargs: "secret",
                ),
                patch(
                    "core.dr_staging_operation_backend.inspect_or_switch",
                    new=lambda **_kwargs: {
                        "before": {"origin_ip": plan.expected_current_ip}
                    },
                ),
            ):
                return await backend.rollback(
                    plan,
                    failed_step="target_term_acquired",
                    completed_steps=("source_fenced",),
                )

        result = asyncio.run(exercise())
        self.assertTrue(result["source_fenced"])
        self.assertTrue(result["target_fenced"])
        self.assertEqual(result["source_active_connections"], 0)
        self.assertEqual(result["target_active_connections"], 0)
        self.assertFalse(result["witness_lease_live"])


if __name__ == "__main__":
    unittest.main()
