from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.dr_command_orchestration_adapter import TYPED_OPERATIONS, load_command_manifest
from core.dr_event_protocol import canonical_json_bytes
from core.dr_failover_orchestrator import (
    ApprovedSigner,
    ApproverPolicy,
    DrOrchestrationError,
    parse_plan,
    run_orchestration,
    verify_two_person_approvals,
)
from core.secure_file_io import verify_hash_chained_jsonl


def command_manifest(*, operation_id: str):
    return {
        "schema": "three-site-typed-operation-adapter-v1",
        "operation_id": operation_id,
        "operations": dict(TYPED_OPERATIONS),
    }


def approved_plan(*, manifest_payload=None):
    operation_id = str(uuid4())
    if manifest_payload is None:
        manifest_payload = command_manifest(operation_id=operation_id)
    else:
        operation_id = manifest_payload["operation_id"]
    private_keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]
    policy_hash = "e" * 64
    signers = {
        f"operator-{number}": ApprovedSigner(
            operator=f"person-{number}",
            key_id=f"operator-{number}",
            custody_domain=f"independent-device-{number}",
            public_key=private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
        )
        for number, private in enumerate(private_keys, 1)
    }
    policy = ApproverPolicy(str(uuid4()), "a" * 40, signers, policy_hash)
    payload = {
        "schema": "three-site-failover-operation-v1",
        "operation_id": operation_id,
        "operation_nonce": str(uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
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
            "campaign_id": str(uuid4()),
            "policy_hash": "d" * 64,
        },
        "readiness_hash": "c" * 64,
        "command_manifest_hash": hashlib.sha256(
            canonical_json_bytes(manifest_payload)
        ).hexdigest(),
        "approver_policy_hash": policy_hash,
        "approvals": [],
    }
    plan_hash = hashlib.sha256(
        canonical_json_bytes({key: value for key, value in payload.items() if key != "approvals"})
    ).hexdigest()
    for number, private in enumerate(private_keys, 1):
        payload["approvals"].append(
            {
                "operator": f"person-{number}",
                "key_id": f"operator-{number}",
                "signature": base64.b64encode(private.sign(plan_hash.encode("ascii"))).decode(),
            }
        )
    return payload, policy


class FakeOperationLedger:
    def __init__(self):
        self.reservation = None
        self.outcome = None

    def _receipt(self, plan, status):
        return {
            "status": status,
            "operation_id": plan.operation_id,
            "operation_nonce": plan.operation_nonce,
            "plan_hash": plan.plan_hash,
            "ledger_receipt_hash": "9" * 64,
            "ledger_receipt_id": "witness-ledger-receipt-1",
        }

    async def reserve(self, plan):
        if self.reservation is None:
            self.reservation = (plan.operation_id, plan.operation_nonce, plan.plan_hash)
            return self._receipt(plan, "reserved")
        if self.reservation != (plan.operation_id, plan.operation_nonce, plan.plan_hash):
            raise DrOrchestrationError("ledger operation identity conflict")
        return self._receipt(plan, "existing")

    async def finalize(self, plan, *, outcome, evidence_hash):
        if len(evidence_hash) != 64:
            raise DrOrchestrationError("ledger evidence hash invalid")
        if self.outcome not in {None, outcome}:
            raise DrOrchestrationError("ledger outcome conflict")
        self.outcome = outcome
        return self._receipt(plan, outcome)


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
        if name == "classification_verified":
            result.update(plan.classification)
        if name == "source_fenced":
            result.update(source_site=plan.source_site, fenced=True)
        if name == "target_ready":
            result.update(target_site=plan.target_site, readiness_hash=plan.readiness_hash)
        if name == "source_connections_drained":
            result.update(source_site=plan.source_site, active_connections=0)
        if name in {"route_switched", "public_route_verified"}:
            result.update(origin_ip=plan.target_ip, domain=plan.domain, record=plan.record)
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
                witness_receipt_hash="f" * 64,
                witness_request_id="rollback-witness-receipt",
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
        raw, policy = approved_plan()
        plan = parse_plan(raw)
        verify_two_person_approvals(plan, policy)
        ledger = FakeOperationLedger()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "failover.jsonl"
            first = FakeAdapter()
            result = await run_orchestration(
                plan, adapter=first, ledger=ledger, journal_path=journal
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(first.calls), 7)
            records = verify_hash_chained_jsonl(journal)
            self.assertEqual(len(records), 9)
            resumed = FakeAdapter()
            await run_orchestration(plan, adapter=resumed, ledger=ledger, journal_path=journal)
            self.assertEqual(resumed.calls, [])

            with self.assertRaisesRegex(DrOrchestrationError, "another controller"):
                await run_orchestration(
                    plan,
                    adapter=FakeAdapter(),
                    ledger=ledger,
                    journal_path=Path(directory) / "lost-journal.jsonl",
                )

    async def test_lost_route_response_rolls_back_and_resume_never_replays_forward_steps(self):
        raw, _ = approved_plan()
        plan = parse_plan(raw)
        ledger = FakeOperationLedger()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "failover.jsonl"
            first = LostRouteResponseAdapter()
            with self.assertRaises(TimeoutError):
                await run_orchestration(plan, adapter=first, ledger=ledger, journal_path=journal)
            self.assertEqual(first.calls[-2:], ["public_route_verified", "rollback"])
            self.assertEqual(ledger.outcome, "rolled_back")
            resumed = FakeAdapter()
            result = await run_orchestration(
                plan, adapter=resumed, ledger=ledger, journal_path=journal
            )
            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(resumed.calls, [])

    async def test_incomplete_rollback_is_retried_before_any_forward_step(self):
        raw, _ = approved_plan()
        plan = parse_plan(raw)
        ledger = FakeOperationLedger()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "failover.jsonl"
            first = RollbackFailsOnceAdapter()
            with self.assertRaises(TimeoutError):
                await run_orchestration(plan, adapter=first, ledger=ledger, journal_path=journal)
            recovery = FakeAdapter()
            result = await run_orchestration(
                plan, adapter=recovery, ledger=ledger, journal_path=journal
            )
            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(recovery.calls, ["rollback"])

    async def test_approval_and_typed_manifest_are_bound_and_fail_closed(self):
        raw, policy = approved_plan()
        raw["approvals"][1]["operator"] = raw["approvals"][0]["operator"]
        plan = parse_plan(raw)
        with self.assertRaises(DrOrchestrationError):
            verify_two_person_approvals(plan, policy)

        operation_id = str(uuid4())
        payload = command_manifest(operation_id=operation_id)
        payload["operations"]["source_fenced"] = "python.-c.arbitrary"
        valid_raw, _ = approved_plan(manifest_payload=payload)
        valid_plan = parse_plan(valid_raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operations.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaises(DrOrchestrationError):
                load_command_manifest(path, plan=valid_plan)

    async def test_expired_new_plan_is_rejected_before_ledger_reservation(self):
        raw, _ = approved_plan()
        raw["generated_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        raw["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        plan = parse_plan(raw)
        ledger = FakeOperationLedger()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DrOrchestrationError, "expired"):
                await run_orchestration(
                    plan,
                    adapter=FakeAdapter(),
                    ledger=ledger,
                    journal_path=Path(directory) / "failover.jsonl",
                )
        self.assertIsNone(ledger.reservation)

    async def test_rollback_after_target_term_rejects_obsolete_source_epoch(self):
        class ObsoleteRollbackAdapter(LostRouteResponseAdapter):
            async def rollback(self, plan, *, failed_step, completed_steps):
                result = await self._result("rollback", plan)
                result["writer_epoch"] = plan.expected_epoch
                return result

        raw, _ = approved_plan()
        plan = parse_plan(raw)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DrOrchestrationError, "source-restored"):
                await run_orchestration(
                    plan,
                    adapter=ObsoleteRollbackAdapter(),
                    ledger=FakeOperationLedger(),
                    journal_path=Path(directory) / "failover.jsonl",
                )

    async def test_typed_manifest_has_no_executable_or_argument_surface(self):
        operation_id = str(uuid4())
        payload = command_manifest(operation_id=operation_id)
        raw, policy = approved_plan(manifest_payload=payload)
        plan = parse_plan(raw)
        verify_two_person_approvals(plan, policy)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operations.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(path, 0o600)
            operations = load_command_manifest(path, plan=plan)
            self.assertEqual(operations, TYPED_OPERATIONS)
            self.assertNotIn("python", json.dumps(operations))
            self.assertNotIn("curl", json.dumps(operations))
            self.assertNotIn("ssh", json.dumps(operations))


if __name__ == "__main__":
    unittest.main()
