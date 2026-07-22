from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
import asyncio
from unittest.mock import patch
from uuid import uuid4

from core.dr_command_orchestration_adapter import TYPED_OPERATIONS, load_command_manifest
from core.dr_event_protocol import canonical_json_bytes
from core.human_approval import approval_subject
from core.human_approval_issuer import (
    authenticate_and_issue,
    create_enrollment,
    totp_code,
)
from core.dr_failover_orchestrator import (
    DrOrchestrationError,
    parse_plan,
    load_approver_policy,
    run_orchestration,
    verify_human_failover_approval,
)
from core.secure_file_io import verify_hash_chained_jsonl


def command_manifest(*, operation_id: str):
    return {
        "schema": "three-site-typed-operation-adapter-v1",
        "operation_id": operation_id,
        "operations": dict(TYPED_OPERATIONS),
    }


def approved_plan(*, manifest_payload=None, rpo_policy=None):
    operation_id = str(uuid4())
    if manifest_payload is None:
        manifest_payload = command_manifest(operation_id=operation_id)
    else:
        operation_id = manifest_payload["operation_id"]
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    enrollment = create_enrollment(
        operator="person-1",
        password="test failover approval passphrase",
        now=generated,
        scrypt_n=2**14,
    )
    policy = enrollment.policy_payload
    policy_hash = hashlib.sha256(canonical_json_bytes(policy)).hexdigest()
    payload = {
        "schema": "three-site-failover-operation-v1",
        "operation_id": operation_id,
        "operation_nonce": str(uuid4()),
        "generated_at": generated.isoformat(),
        "expires_at": (generated + timedelta(minutes=5)).isoformat(),
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
        "rpo_policy": rpo_policy or {
            "mode": "zero_loss",
            "max_unreplicated_events": 0,
            "approval_reason": None,
            "approval_ticket": None,
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
    subject = approval_subject(
        artifact_type="three-site-failover-operation-v1",
        artifact_sha256=plan_hash,
        release_sha=payload["release_sha"],
        bindings={
            "operation_id": payload["operation_id"],
            "operation_nonce": payload["operation_nonce"],
            "source_site": payload["source_site"],
            "target_site": payload["target_site"],
            "expected_epoch": payload["expected_epoch"],
            "target_epoch": payload["target_epoch"],
            "readiness_hash": payload["readiness_hash"],
            "command_manifest_hash": payload["command_manifest_hash"],
        },
    )
    token, _state, _audit = authenticate_and_issue(
        secrets_payload=enrollment.secrets_payload,
        state_payload=enrollment.state_payload,
        policy_payload=policy,
        private_key_envelope=enrollment.private_key_envelope,
        password="test failover approval passphrase",
        totp=totp_code(enrollment.totp_secret, at=generated)[1],
        recovery_code=None,
        action="promote_ir",
        environment="staging",
        subject=subject,
        ttl_seconds=300,
        now=generated,
    )
    payload["approvals"] = [token]
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
            boundary = {
                "mode": "proven",
                "origin_site": plan.source_site,
                "target_site": plan.target_site,
                "producer_epoch": plan.expected_epoch,
                "final_sequence": 12,
                "final_transaction_hash": "8" * 64,
                "estimated_unreplicated_events": 0,
            }
            boundary["boundary_hash"] = hashlib.sha256(
                canonical_json_bytes(boundary)
            ).hexdigest()
            result.update(
                source_site=plan.source_site,
                fenced=True,
                active_connections=0,
                boundary_captured_after_drain=True,
                admission_fence=True,
                control_state="fenced",
                witness_drain_request_id="drain-request",
                witness_drain_receipt_hash="d" * 64,
                source_tail_boundary=boundary,
            )
        if name == "source_connections_drained":
            result.update(
                source_site=plan.source_site,
                active_connections=0,
                admission_fence=True,
                control_state="fenced",
            )
        if name in {"route_switched", "public_route_verified"}:
            result.update(origin_ip=plan.target_ip, domain=plan.domain, record=plan.record)
        if name == "rollback":
            result.update(
                rollback_state="safe_fenced",
                source_site=plan.source_site,
                target_site=plan.target_site,
                target_fenced=True,
                target_active_connections=0,
                source_fenced=True,
                source_active_connections=0,
                witness_lease_live=False,
                holder_site=None,
                origin_ip=plan.expected_current_ip,
                domain=plan.domain,
                record=plan.record,
                witness_receipt_hash="f" * 64,
                witness_request_id="rollback-witness-receipt",
            )
        return result

    async def classification_verified(self, plan): return await self._result("classification_verified", plan)
    async def source_fenced(self, plan): return await self._result("source_fenced", plan)
    async def target_ready(self, plan, *, source_tail_boundary):
        result = await self._result("target_ready", plan)
        result.update(
            target_site=plan.target_site,
            readiness_hash=plan.readiness_hash,
            source_tail_boundary_hash=source_tail_boundary["boundary_hash"],
            target_applied_sequence=source_tail_boundary["final_sequence"],
            target_applied_through_boundary=True,
        )
        return result
    async def target_term_acquired(self, plan): return await self._result("target_term_acquired", plan)
    async def source_connections_drained(self, plan): return await self._result("source_connections_drained", plan)
    async def route_switched(self, plan): return await self._result("route_switched", plan)
    async def public_route_verified(self, plan): return await self._result("public_route_verified", plan)
    async def rollback(self, plan, *, failed_step, completed_steps):
        result = await self._result("rollback", plan)
        return result


class LostRouteResponseAdapter(FakeAdapter):
    async def public_route_verified(self, plan):
        del plan
        self.calls.append("public_route_verified")
        raise TimeoutError("provider response lost")


class SourceUnavailableAdapter(FakeAdapter):
    """Model an independently fenced source whose final tail cannot be read."""

    def __init__(self, *, estimated_unreplicated_events: int):
        super().__init__()
        self.estimated_unreplicated_events = estimated_unreplicated_events

    async def source_fenced(self, plan):
        result = await self._result("source_fenced", plan)
        boundary = {
            "mode": "approved_rpo_exception",
            "origin_site": plan.source_site,
            "target_site": plan.target_site,
            "producer_epoch": plan.expected_epoch,
            # This is the last immutable tail that the target can prove.  The
            # separately approved estimate describes the unavailable suffix.
            "final_sequence": 10,
            "final_transaction_hash": "7" * 64,
            "estimated_unreplicated_events": self.estimated_unreplicated_events,
        }
        boundary["boundary_hash"] = hashlib.sha256(
            canonical_json_bytes(boundary)
        ).hexdigest()
        result["source_tail_boundary"] = boundary
        return result

    async def target_ready(self, plan, *, source_tail_boundary):
        result = await self._result("target_ready", plan)
        result.update(
            target_site=plan.target_site,
            readiness_hash=plan.readiness_hash,
            source_tail_boundary_hash=source_tail_boundary["boundary_hash"],
            target_applied_sequence=source_tail_boundary["final_sequence"],
            target_applied_through_boundary=True,
        )
        return result


class RollbackFailsOnceAdapter(LostRouteResponseAdapter):
    async def rollback(self, plan, *, failed_step, completed_steps):
        del plan, failed_step, completed_steps
        self.calls.append("rollback")
        raise TimeoutError("rollback response lost")


class DrFailoverOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_two_device_failover_policy_is_rejected(self):
        policy = {
            "schema": "three-site-failover-approver-policy-v1",
            "policy_id": str(uuid4()),
            "release_sha": "a" * 40,
            "minimum_approvals": 2,
            "signers": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(DrOrchestrationError, "human approval policy"):
                load_approver_policy(path)

    async def test_plan_rejects_coerced_epochs_and_route_scope_drift(self):
        raw, _policy = approved_plan()
        raw["expected_epoch"] = "7"
        with self.assertRaisesRegex(DrOrchestrationError, "exact integers"):
            parse_plan(raw)

        raw, _policy = approved_plan()
        raw["record"] = "other"
        with self.assertRaisesRegex(DrOrchestrationError, "failover-test domain"):
            parse_plan(raw)

        raw, _policy = approved_plan()
        raw["target_ip"] = "not-an-ip"
        with self.assertRaisesRegex(DrOrchestrationError, "origin IP"):
            parse_plan(raw)

    async def test_source_unavailable_path_is_not_advertised_without_concrete_backend(self):
        raw, policy = approved_plan(
            rpo_policy={
                "mode": "bounded_loss",
                "max_unreplicated_events": 3,
                "approval_reason": "Source storage is unavailable after host loss",
                "approval_ticket": "INC-2026-0719",
            }
        )
        del policy
        with self.assertRaisesRegex(DrOrchestrationError, "RPO policy is invalid"):
            parse_plan(raw)

    async def test_source_unavailable_tail_is_rejected_by_zero_loss_policy(self):
        raw, policy = approved_plan()
        plan = parse_plan(raw)
        verify_human_failover_approval(plan, policy)
        adapter = SourceUnavailableAdapter(estimated_unreplicated_events=1)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DrOrchestrationError, "boundary mode"):
                await run_orchestration(
                    plan,
                    adapter=adapter,
                    ledger=FakeOperationLedger(),
                    journal_path=Path(directory) / "failover.jsonl",
                )
        self.assertEqual(adapter.calls, ["classification_verified", "source_fenced", "rollback"])

    async def test_source_unavailable_budget_is_rejected_at_plan_parse(self):
        raw, policy = approved_plan(
            rpo_policy={
                "mode": "bounded_loss",
                "max_unreplicated_events": 2,
                "approval_reason": "Emergency promotion after independent source loss",
                "approval_ticket": "INC-2026-0720",
            }
        )
        del policy
        with self.assertRaisesRegex(DrOrchestrationError, "RPO policy is invalid"):
            parse_plan(raw)

    async def test_human_approved_plan_runs_once_and_resumes_from_hash_chained_journal(self):
        raw, policy = approved_plan()
        plan = parse_plan(raw)
        verify_human_failover_approval(plan, policy)
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
            self.assertEqual(len(records), 16)
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
        raw["approvals"][0]["operator"] = "attacker"
        plan = parse_plan(raw)
        with self.assertRaises(DrOrchestrationError):
            verify_human_failover_approval(plan, policy)

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

    async def test_expired_reserved_plan_never_resumes_forward_steps(self):
        class InterruptedAfterClassification(FakeAdapter):
            async def source_fenced(self, plan):
                del plan
                raise asyncio.CancelledError()

        raw, _ = approved_plan()
        plan = parse_plan(raw)
        ledger = FakeOperationLedger()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "failover.jsonl"
            with self.assertRaises(asyncio.CancelledError):
                await run_orchestration(
                    plan,
                    adapter=InterruptedAfterClassification(),
                    ledger=ledger,
                    journal_path=journal,
                )
            resumed = FakeAdapter()
            with patch(
                "core.dr_failover_orchestrator.validate_plan_freshness",
                side_effect=DrOrchestrationError("orchestration plan has expired"),
            ):
                result = await run_orchestration(
                    plan,
                    adapter=resumed,
                    ledger=ledger,
                    journal_path=journal,
                )
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(result["rollback_state"], "safe_fenced")
        self.assertEqual(resumed.calls, ["rollback"])

    async def test_expired_plan_after_source_fence_performs_only_rollback(self):
        class InterruptedBeforeDrain(FakeAdapter):
            async def source_connections_drained(self, plan):
                del plan
                raise asyncio.CancelledError()

        raw, _ = approved_plan()
        plan = parse_plan(raw)
        ledger = FakeOperationLedger()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "failover.jsonl"
            with self.assertRaises(asyncio.CancelledError):
                await run_orchestration(
                    plan,
                    adapter=InterruptedBeforeDrain(),
                    ledger=ledger,
                    journal_path=journal,
                )
            resumed = FakeAdapter()
            with patch(
                "core.dr_failover_orchestrator.validate_plan_freshness",
                side_effect=DrOrchestrationError("orchestration plan has expired"),
            ):
                result = await run_orchestration(
                    plan,
                    adapter=resumed,
                    ledger=ledger,
                    journal_path=journal,
                )
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(resumed.calls, ["rollback"])

    async def test_expiry_at_each_saga_boundary_blocks_every_later_forward_step(self):
        forward_steps = (
            "classification_verified",
            "source_fenced",
            "source_connections_drained",
            "target_ready",
            "target_term_acquired",
            "route_switched",
            "public_route_verified",
        )

        for expiry_step in forward_steps:
            with self.subTest(expiry_step=expiry_step):
                class ExpiringAdapter(FakeAdapter):
                    def __init__(self):
                        super().__init__()
                        self.expired = False

                    async def _result(self, name, plan):
                        result = await super()._result(name, plan)
                        if name == expiry_step:
                            self.expired = True
                        return result

                raw, _ = approved_plan()
                plan = parse_plan(raw)
                ledger = FakeOperationLedger()
                adapter = ExpiringAdapter()

                def boundary_freshness(_plan):
                    if adapter.expired:
                        raise DrOrchestrationError("orchestration plan has expired")

                with tempfile.TemporaryDirectory() as directory, patch(
                    "core.dr_failover_orchestrator.validate_plan_freshness",
                    side_effect=boundary_freshness,
                ):
                    result = await run_orchestration(
                        plan,
                        adapter=adapter,
                        ledger=ledger,
                        journal_path=Path(directory) / "failover.jsonl",
                    )

                expected_forward = forward_steps[: forward_steps.index(expiry_step) + 1]
                self.assertEqual(result["status"], "rolled_back")
                self.assertEqual(result["reason"], "plan_expired")
                self.assertEqual(ledger.outcome, "rolled_back")
                if expiry_step == "classification_verified":
                    self.assertEqual(adapter.calls, list(expected_forward))
                    self.assertEqual(result["rollback_state"], "not_started")
                else:
                    self.assertEqual(adapter.calls, [*expected_forward, "rollback"])

    async def test_rollback_after_target_term_rejects_obsolete_source_epoch(self):
        class ObsoleteRollbackAdapter(LostRouteResponseAdapter):
            async def rollback(self, plan, *, failed_step, completed_steps):
                result = await self._result("rollback", plan)
                result["rollback_state"] = "source_restored"
                return result

        raw, _ = approved_plan()
        plan = parse_plan(raw)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DrOrchestrationError, "safe-fenced"):
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
        verify_human_failover_approval(plan, policy)
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
