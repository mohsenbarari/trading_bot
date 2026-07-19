from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.dr_command_orchestration_adapter import load_command_manifest
from core.dr_event_protocol import canonical_json_bytes
from core.dr_failover_orchestrator import (
    DrOrchestrationError,
    parse_plan,
    run_orchestration,
    verify_two_person_approvals,
)
from core.secure_file_io import verify_hash_chained_jsonl


COMMAND_STEPS = (
    "source_fenced", "target_ready", "target_term_acquired",
    "source_connections_drained", "route_switched", "public_route_verified", "rollback",
)


def command_manifest(*, operation_id: str, executable: str = "/usr/bin/python3"):
    return {
        "schema": "three-site-command-adapter-v1",
        "operation_id": operation_id,
        "commands": {step: [executable, "-V"] for step in COMMAND_STEPS},
    }


def approved_plan(*, manifest_payload=None):
    operation_id = str(uuid4())
    if manifest_payload is None:
        manifest_payload = command_manifest(operation_id=operation_id)
    else:
        operation_id = manifest_payload["operation_id"]
    payload = {
        "schema": "three-site-failover-operation-v1",
        "operation_id": operation_id,
        "action": "promote_ir",
        "source_site": "webapp_fi",
        "target_site": "webapp_ir",
        "expected_epoch": 7,
        "target_epoch": 8,
        "release_sha": "a" * 40,
        "domain": "gold-trading.ir",
        "record": "app",
        "expected_current_ip": "192.0.2.10",
        "target_ip": "192.0.2.20",
        "classification": {
            "mode": "isolated",
            "confidence": "high",
            "consecutive_rounds": 3,
            "evidence_hash": "b" * 64,
        },
        "readiness_hash": "c" * 64,
        "command_manifest_hash": hashlib.sha256(
            canonical_json_bytes(manifest_payload)
        ).hexdigest(),
        "approvals": [],
    }
    plan_hash = hashlib.sha256(
        canonical_json_bytes({key: value for key, value in payload.items() if key != "approvals"})
    ).hexdigest()
    public_keys = {}
    for number in (1, 2):
        private = Ed25519PrivateKey.generate()
        key_id = f"operator-{number}"
        public_keys[key_id] = base64.b64encode(
            private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode("ascii")
        payload["approvals"].append(
            {
                "operator": f"person-{number}",
                "key_id": key_id,
                "signature": base64.b64encode(private.sign(plan_hash.encode("ascii"))).decode("ascii"),
            }
        )
    return payload, public_keys


class FakeAdapter:
    def __init__(self):
        self.calls = []

    async def _result(self, name, plan):
        self.calls.append(name)
        result = {
            "status": "ok",
            "operation_id": plan.operation_id,
            "evidence_hash": hashlib.sha256(name.encode()).hexdigest(),
        }
        if name == "target_term_acquired":
            result.update(holder_site=plan.target_site, writer_epoch=plan.target_epoch)
        if name == "source_fenced":
            result.update(source_site=plan.source_site, fenced=True)
        if name == "target_ready":
            result.update(target_site=plan.target_site, readiness_hash=plan.readiness_hash)
        if name == "source_connections_drained":
            result.update(source_site=plan.source_site, active_connections=0)
        if name in {"route_switched", "public_route_verified"}:
            result.update(
                origin_ip=plan.target_ip,
                domain=plan.domain,
                record=plan.record,
            )
        if name == "rollback":
            result.update(
                rollback_state="source_restored",
                source_site=plan.source_site,
                target_site=plan.target_site,
                target_fenced=True,
                target_active_connections=0,
                holder_site=plan.source_site,
                writer_epoch=plan.target_epoch + 1,
                origin_ip=plan.expected_current_ip,
                domain=plan.domain,
                record=plan.record,
            )
        return result

    async def classification_verified(self, plan): return await self._result("classification_verified", plan)
    async def source_fenced(self, plan): return await self._result("source_fenced", plan)
    async def target_ready(self, plan): return await self._result("target_ready", plan)
    async def target_term_acquired(self, plan): return await self._result("target_term_acquired", plan)
    async def source_connections_drained(self, plan): return await self._result("source_connections_drained", plan)
    async def route_switched(self, plan): return await self._result("route_switched", plan)
    async def public_route_verified(self, plan): return await self._result("public_route_verified", plan)
    async def rollback(self, plan, *, failed_step, completed_steps):
        del failed_step, completed_steps
        return await self._result("rollback", plan)


class LostRouteResponseAdapter(FakeAdapter):
    async def public_route_verified(self, plan):
        del plan
        self.calls.append("public_route_verified")
        raise TimeoutError("provider response lost")


class RollbackFailsOnceAdapter(LostRouteResponseAdapter):
    async def rollback(self, plan, *, failed_step, completed_steps):
        del plan, failed_step, completed_steps
        self.calls.append("rollback")
        raise TimeoutError("rollback response lost")


class DrFailoverOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_person_plan_runs_once_and_resumes_from_hash_chained_journal(self):
        raw, public_keys = approved_plan()
        plan = parse_plan(raw)
        verify_two_person_approvals(plan, public_keys)
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "failover.jsonl"
            first = FakeAdapter()
            result = await run_orchestration(plan, adapter=first, journal_path=journal)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(first.calls), 7)
            records = verify_hash_chained_jsonl(journal)
            self.assertEqual(len(records), 8)
            resumed = FakeAdapter()
            await run_orchestration(plan, adapter=resumed, journal_path=journal)
            self.assertEqual(resumed.calls, [])

    async def test_lost_route_response_rolls_back_and_resume_never_replays_forward_steps(self):
        raw, _ = approved_plan()
        plan = parse_plan(raw)
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "failover.jsonl"
            first = LostRouteResponseAdapter()
            with self.assertRaises(TimeoutError):
                await run_orchestration(plan, adapter=first, journal_path=journal)
            self.assertEqual(first.calls[-2:], ["public_route_verified", "rollback"])
            records = verify_hash_chained_jsonl(journal)
            self.assertEqual(records[-1]["event"], "dr.orchestration.rollback_completed")

            resumed = FakeAdapter()
            result = await run_orchestration(plan, adapter=resumed, journal_path=journal)
            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(resumed.calls, [])

    async def test_incomplete_rollback_is_retried_before_any_forward_step(self):
        raw, _ = approved_plan()
        plan = parse_plan(raw)
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "failover.jsonl"
            first = RollbackFailsOnceAdapter()
            with self.assertRaises(TimeoutError):
                await run_orchestration(plan, adapter=first, journal_path=journal)
            self.assertEqual(first.calls[-1], "rollback")

            recovery = FakeAdapter()
            result = await run_orchestration(plan, adapter=recovery, journal_path=journal)
            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(recovery.calls, ["rollback"])

    async def test_approval_and_command_manifest_are_bound_and_fail_closed(self):
        raw, public_keys = approved_plan()
        raw["approvals"][1]["operator"] = raw["approvals"][0]["operator"]
        plan = parse_plan(raw)
        with self.assertRaises(DrOrchestrationError):
            verify_two_person_approvals(plan, public_keys)

        operation_id = str(uuid4())
        payload = command_manifest(operation_id=operation_id, executable="/bin/sh")
        valid_raw, _ = approved_plan(manifest_payload=payload)
        valid_plan = parse_plan(valid_raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaises(DrOrchestrationError):
                load_command_manifest(path, plan=valid_plan)

    async def test_command_manifest_contents_are_covered_by_operator_signatures(self):
        operation_id = str(uuid4())
        payload = command_manifest(operation_id=operation_id)
        raw, public_keys = approved_plan(manifest_payload=payload)
        plan = parse_plan(raw)
        verify_two_person_approvals(plan, public_keys)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(path, 0o600)
            commands = load_command_manifest(path, plan=plan)
            self.assertEqual(commands["source_fenced"][0], "/usr/bin/python3")

            payload["commands"]["source_fenced"] = ["/usr/bin/curl", "https://example.invalid"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(DrOrchestrationError):
                load_command_manifest(path, plan=plan)


if __name__ == "__main__":
    unittest.main()
