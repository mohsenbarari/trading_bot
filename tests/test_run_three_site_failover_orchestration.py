from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core.dr_failover_orchestrator import DrOrchestrationError
from core.secure_file_io import append_hash_chained_jsonl
from scripts.run_three_site_failover_orchestration import (
    _journal_proves_operation_started,
    confirmation_phrase,
    prepare,
    run,
)


def _args(*, apply: bool, confirm: str | None = None):  # noqa: ANN202
    return SimpleNamespace(
        apply=apply,
        confirm=confirm,
        journal=Path("/secure/failover.jsonl"),
    )


def _prepared():  # noqa: ANN202
    plan = SimpleNamespace(
        operation_id="11111111-1111-4111-8111-111111111111",
        plan_hash="a" * 64,
        action="promote_ir",
        source_site="webapp_fi",
        target_site="webapp_ir",
        domain="gold-trading.ir",
        record="app",
    )
    backend = SimpleNamespace(
        config=SimpleNamespace(
            witness_config=object(),
            witness_public_key="public-key",
        )
    )
    return plan, {"typed": "operation"}, backend


class ThreeSiteFailoverRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_uses_backend_bound_witness_key_for_plan_approval(self):
        plan, operations, backend = _prepared()
        args = SimpleNamespace(
            plan=Path("/secure/plan.json"),
            human_approval_policy=Path("/secure/policy.json"),
            journal=Path("/secure/journal.jsonl"),
            backend_config=Path("/secure/backend.json"),
            inventory=Path("/secure/inventory.json"),
            inventory_approval=Path("/secure/inventory-approval.json"),
            inventory_approval_policy=Path("/secure/inventory-policy.json"),
            command_manifest=Path("/secure/commands.json"),
        )
        config = backend.config
        typed_backend = SimpleNamespace(config=config, preflight=Mock())
        with (
            patch(
                "scripts.run_three_site_failover_orchestration._strict_json",
                side_effect=[
                    {"plan": True},
                    {"inventory": True},
                    {"inventory_approval": True},
                    {"inventory_policy": True},
                ],
            ),
            patch(
                "scripts.run_three_site_failover_orchestration.parse_plan",
                return_value=plan,
            ),
            patch(
                "scripts.run_three_site_failover_orchestration.load_approver_policy",
                return_value={"policy": True},
            ),
            patch(
                "scripts.run_three_site_failover_orchestration.load_staging_backend_config",
                return_value=config,
            ) as load_config,
            patch(
                "scripts.run_three_site_failover_orchestration.verify_human_failover_approval",
            ) as verify_approval,
            patch(
                "scripts.run_three_site_failover_orchestration.load_typed_operation_manifest",
                return_value=operations,
            ),
            patch(
                "scripts.run_three_site_failover_orchestration.StagingTypedOperationBackend",
                return_value=typed_backend,
            ),
        ):
            prepared = prepare(args)
        load_config.assert_called_once()
        verify_approval.assert_called_once_with(
            plan,
            {"policy": True},
            require_fresh=True,
            witness_relay_public_key="public-key",
        )
        typed_backend.preflight.assert_called_once_with(plan)
        self.assertEqual(prepared, (plan, operations, typed_backend))

    async def test_historical_approval_requires_exact_reserved_operation_journal(self):
        plan, _operations, _backend = _prepared()
        with tempfile.TemporaryDirectory() as temporary:
            journal = Path(temporary) / "failover.jsonl"
            self.assertFalse(_journal_proves_operation_started(journal, plan))
            append_hash_chained_jsonl(
                journal,
                {
                    "event": "dr.orchestration.operation_reserved",
                    "operation_id": "22222222-2222-4222-8222-222222222222",
                    "plan_hash": plan.plan_hash,
                },
            )
            self.assertFalse(_journal_proves_operation_started(journal, plan))
        with tempfile.TemporaryDirectory() as temporary:
            journal = Path(temporary) / "failover.jsonl"
            append_hash_chained_jsonl(
                journal,
                {
                    "event": "dr.orchestration.operation_reserved",
                    "operation_id": plan.operation_id,
                    "plan_hash": plan.plan_hash,
                },
            )
            self.assertTrue(_journal_proves_operation_started(journal, plan))

    async def test_dry_run_validates_package_without_ledger_or_mutation(self):
        plan, operations, backend = _prepared()
        with (
            patch(
                "scripts.run_three_site_failover_orchestration.prepare",
                return_value=(plan, operations, backend),
            ),
            patch(
                "scripts.run_three_site_failover_orchestration.validate_plan_freshness"
            ) as freshness,
            patch(
                "scripts.run_three_site_failover_orchestration.run_orchestration",
                new=AsyncMock(),
            ) as orchestration,
        ):
            result = await run(_args(apply=False))
        freshness.assert_called_once_with(plan)
        orchestration.assert_not_awaited()
        self.assertEqual(result["status"], "planned")
        self.assertEqual(
            result["required_confirmation"],
            confirmation_phrase(plan.operation_id, plan.plan_hash),
        )

    async def test_apply_requires_exact_plan_bound_confirmation(self):
        prepared = _prepared()
        with patch(
            "scripts.run_three_site_failover_orchestration.prepare",
            return_value=prepared,
        ):
            with self.assertRaisesRegex(DrOrchestrationError, "confirmation"):
                await run(_args(apply=True, confirm="wrong"))

    async def test_apply_injects_typed_backend_and_independent_ledger(self):
        plan, operations, backend = _prepared()
        expected = {"status": "completed", "operation_id": plan.operation_id}
        required = confirmation_phrase(plan.operation_id, plan.plan_hash)
        with (
            patch(
                "scripts.run_three_site_failover_orchestration.prepare",
                return_value=(plan, operations, backend),
            ),
            patch(
                "scripts.run_three_site_failover_orchestration.TypedOrchestrationAdapter"
            ) as adapter_class,
            patch(
                "scripts.run_three_site_failover_orchestration.WitnessOperationLedger"
            ) as ledger_class,
            patch(
                "scripts.run_three_site_failover_orchestration.run_orchestration",
                new=AsyncMock(return_value=expected),
            ) as orchestration,
        ):
            result = await run(_args(apply=True, confirm=required))
        adapter_class.assert_called_once_with(operations, backend=backend)
        ledger_class.assert_called_once_with(
            backend.config.witness_config,
            witness_public_key=backend.config.witness_public_key,
        )
        orchestration.assert_awaited_once_with(
            plan,
            adapter=adapter_class.return_value,
            ledger=ledger_class.return_value,
            journal_path=Path("/secure/failover.jsonl"),
        )
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
