from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import unittest

from core.dedicated_object_delta_atomic_applier import (
    DEDICATED_OBJECT_DELTA_APPLIER_CONTRACT,
    IMPORT_ACTION_APPLY,
    IMPORT_ACTION_REPLAY,
    REQUIRED_DEDICATED_OBJECT_DELTA_APPLIER_INVARIANTS,
    DedicatedObjectDeltaAtomicApplyError,
    apply_atomic_object_delta_plan,
)
from core.object_delta_import_plan import AtomicObjectDeltaImportPlan
from core.object_delta_mvp_canonical import INSERT, validate_canonical_mvp_object_delta
from core.object_delta_receiver_mvp_handlers import (
    ObjectDeltaReceiverMvpPlannedChange,
    compile_object_delta_mvp_receiver_planned_change,
)


@dataclass(frozen=True)
class _Change:
    logical_sequence: int
    identity: str


@dataclass(frozen=True)
class _Plan:
    action: str
    changes_to_apply: tuple[_Change, ...]
    receipt_to_insert: object | None
    cursor_to_write: object | None


class _Transaction:
    contract_name = DEDICATED_OBJECT_DELTA_APPLIER_CONTRACT

    def __init__(self, *, fail_on: str | None = None, rollback_fails: bool = False) -> None:
        self.fail_on = fail_on
        self.rollback_fails = rollback_fails
        self.calls: list[str] = []

    async def apply_db_change(self, change: ObjectDeltaReceiverMvpPlannedChange) -> None:
        identity = change.intent.name
        self.calls.append(f"apply:{identity}")
        if self.fail_on == identity:
            raise RuntimeError(f"apply failed: {identity}")

    async def insert_immutable_receipt(self, receipt: object) -> None:
        self.calls.append("receipt")
        if self.fail_on == "receipt":
            raise RuntimeError("receipt failed")

    async def write_receiver_cursor(self, cursor: object) -> None:
        self.calls.append("cursor")
        if self.fail_on == "cursor":
            raise RuntimeError("cursor failed")

    async def commit(self) -> None:
        self.calls.append("commit")
        if self.fail_on == "commit":
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.calls.append("rollback")
        if self.rollback_fails:
            raise RuntimeError("rollback failed")


class _Adapter:
    contract_name = DEDICATED_OBJECT_DELTA_APPLIER_CONTRACT

    def __init__(self, transaction: _Transaction) -> None:
        self.transaction = transaction
        self.begin_count = 0

    async def begin_atomic_object_delta_apply(self) -> _Transaction:
        self.begin_count += 1
        return self.transaction


def _receiver_change(sequence: int, name: str) -> ObjectDeltaReceiverMvpPlannedChange:
    return compile_object_delta_mvp_receiver_planned_change(
        logical_sequence=sequence,
        change_log_id=700 + sequence,
        descriptor=validate_canonical_mvp_object_delta(
            {
                "table": "commodities",
                "operation": INSERT,
                "identity": {"name": name},
                "fields": {},
                "references": {},
            }
        ),
    )


def _apply_plan(
    *, changes: tuple[ObjectDeltaReceiverMvpPlannedChange, ...] | None = None
) -> AtomicObjectDeltaImportPlan:
    return AtomicObjectDeltaImportPlan(
        action=IMPORT_ACTION_APPLY,
        changes_to_apply=changes
        if changes is not None
        else (_receiver_change(1, "one"), _receiver_change(2, "two")),
        receipt_to_insert={"receipt": "immutable"},
        cursor_to_write={"cursor": "next"},
    )


class DedicatedObjectDeltaAtomicApplierTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_uses_one_transaction_and_commits_only_after_all_db_writes(self) -> None:
        transaction = _Transaction()
        adapter = _Adapter(transaction)

        result = await apply_atomic_object_delta_plan(plan=_apply_plan(), adapter=adapter)

        self.assertEqual(IMPORT_ACTION_APPLY, result.action)
        self.assertEqual(2, result.changes_applied)
        self.assertEqual(1, adapter.begin_count)
        self.assertEqual(
            ["apply:one", "apply:two", "receipt", "cursor", "commit"], transaction.calls
        )

    async def test_replay_is_zero_mutation_and_does_not_open_a_transaction(self) -> None:
        transaction = _Transaction()
        adapter = _Adapter(transaction)
        replay = AtomicObjectDeltaImportPlan(
            action=IMPORT_ACTION_REPLAY,
            changes_to_apply=(),
            receipt_to_insert=None,
            cursor_to_write=None,
        )

        result = await apply_atomic_object_delta_plan(plan=replay, adapter=adapter)

        self.assertEqual(IMPORT_ACTION_REPLAY, result.action)
        self.assertEqual(0, result.changes_applied)
        self.assertEqual(0, adapter.begin_count)
        self.assertEqual([], transaction.calls)

    async def test_invalid_plan_fails_before_opening_any_transaction(self) -> None:
        transaction = _Transaction()
        adapter = _Adapter(transaction)

        with self.assertRaisesRegex(DedicatedObjectDeltaAtomicApplyError, "contiguous"):
            await apply_atomic_object_delta_plan(
                plan=_apply_plan(
                    changes=(_receiver_change(2, "first"), _receiver_change(4, "skipped"))
                ),
                adapter=adapter,
            )

        self.assertEqual(0, adapter.begin_count)
        self.assertEqual([], transaction.calls)

    async def test_later_contiguous_batch_is_accepted_without_requiring_genesis_sequence(self) -> None:
        transaction = _Transaction()

        result = await apply_atomic_object_delta_plan(
            plan=_apply_plan(
                changes=(_receiver_change(41, "later-one"), _receiver_change(42, "later-two"))
            ),
            adapter=_Adapter(transaction),
        )

        self.assertEqual(2, result.changes_applied)
        self.assertEqual(
            ["apply:later-one", "apply:later-two", "receipt", "cursor", "commit"],
            transaction.calls,
        )

    async def test_apply_failure_rolls_back_without_receipt_cursor_or_commit(self) -> None:
        transaction = _Transaction(fail_on="two")
        adapter = _Adapter(transaction)

        with self.assertRaisesRegex(RuntimeError, "apply failed"):
            await apply_atomic_object_delta_plan(plan=_apply_plan(), adapter=adapter)

        self.assertEqual(["apply:one", "apply:two", "rollback"], transaction.calls)

    async def test_receipt_cursor_or_commit_failure_rolls_back_the_same_transaction(self) -> None:
        for failure, expected_calls in (
            ("receipt", ["apply:one", "apply:two", "receipt", "rollback"]),
            ("cursor", ["apply:one", "apply:two", "receipt", "cursor", "rollback"]),
            ("commit", ["apply:one", "apply:two", "receipt", "cursor", "commit", "rollback"]),
        ):
            with self.subTest(failure=failure):
                transaction = _Transaction(fail_on=failure)

                with self.assertRaisesRegex(RuntimeError, f"{failure} failed"):
                    await apply_atomic_object_delta_plan(
                        plan=_apply_plan(), adapter=_Adapter(transaction)
                    )

                self.assertEqual(expected_calls, transaction.calls)

    async def test_rollback_failure_is_reported_as_a_fail_closed_contract_error(self) -> None:
        transaction = _Transaction(fail_on="one", rollback_fails=True)

        with self.assertRaisesRegex(DedicatedObjectDeltaAtomicApplyError, "rollback failed"):
            await apply_atomic_object_delta_plan(plan=_apply_plan(), adapter=_Adapter(transaction))

        self.assertEqual(["apply:one", "rollback"], transaction.calls)

    async def test_adapter_and_transaction_must_explicitly_declare_the_dedicated_contract(self) -> None:
        transaction = _Transaction()
        adapter = _Adapter(transaction)
        adapter.contract_name = "generic-session"

        with self.assertRaisesRegex(DedicatedObjectDeltaAtomicApplyError, "adapter"):
            await apply_atomic_object_delta_plan(plan=_apply_plan(), adapter=adapter)

        self.assertEqual(0, adapter.begin_count)

    async def test_invalid_transaction_contract_is_rolled_back_immediately_after_begin(self) -> None:
        transaction = _Transaction()
        transaction.contract_name = "generic-session"

        with self.assertRaisesRegex(DedicatedObjectDeltaAtomicApplyError, "transaction"):
            await apply_atomic_object_delta_plan(plan=_apply_plan(), adapter=_Adapter(transaction))

        self.assertEqual(["rollback"], transaction.calls)

    async def test_structural_or_generic_changes_cannot_reach_the_applier(self) -> None:
        transaction = _Transaction()
        adapter = _Adapter(transaction)
        generic_plan = _Plan(
            action=IMPORT_ACTION_APPLY,
            changes_to_apply=(_Change(1, "unreviewed"),),
            receipt_to_insert={"receipt": "immutable"},
            cursor_to_write={"cursor": "next"},
        )

        with self.assertRaisesRegex(DedicatedObjectDeltaAtomicApplyError, "import plan is invalid"):
            await apply_atomic_object_delta_plan(plan=generic_plan, adapter=adapter)

        self.assertEqual(0, adapter.begin_count)
        self.assertEqual([], transaction.calls)

    async def test_direct_or_replaced_changes_cannot_be_smuggled_inside_a_real_atomic_plan(self) -> None:
        valid_plan = _apply_plan()
        direct_change = ObjectDeltaReceiverMvpPlannedChange(
            logical_sequence=1,
            change_log_id=701,
            execution_registry_fingerprint=valid_plan.changes_to_apply[0].execution_registry_fingerprint,
            intent=valid_plan.changes_to_apply[0].intent,
        )
        direct_plan = AtomicObjectDeltaImportPlan(
            action=IMPORT_ACTION_APPLY,
            changes_to_apply=(direct_change, valid_plan.changes_to_apply[1]),
            receipt_to_insert=valid_plan.receipt_to_insert,
            cursor_to_write=valid_plan.cursor_to_write,
        )
        replaced_plan = replace(
            valid_plan,
            changes_to_apply=(replace(valid_plan.changes_to_apply[0]), valid_plan.changes_to_apply[1]),
        )

        for plan in (direct_plan, replaced_plan):
            with self.subTest(plan=plan):
                transaction = _Transaction()
                adapter = _Adapter(transaction)
                with self.assertRaisesRegex(
                    DedicatedObjectDeltaAtomicApplyError,
                    "authorized receiver handler intent",
                ):
                    await apply_atomic_object_delta_plan(plan=plan, adapter=adapter)
                self.assertEqual(0, adapter.begin_count)
                self.assertEqual([], transaction.calls)


class DedicatedObjectDeltaAtomicApplierStaticContractTests(unittest.TestCase):
    def test_contract_explicitly_forbids_side_effecting_behaviour(self) -> None:
        invariants = "\n".join(REQUIRED_DEDICATED_OBJECT_DELTA_APPLIER_INVARIANTS)
        for prohibited in ("realtime", "Telegram", "HTTP", "cache", "audit", "legacy sync"):
            self.assertIn(prohibited, invariants)

    def test_module_has_no_runtime_transport_or_database_dependencies(self) -> None:
        path = Path(__file__).parents[1] / "core/dedicated_object_delta_atomic_applier.py"
        source = path.read_text(encoding="utf-8")
        for prohibited_import in (
            "import sqlalchemy",
            "from sqlalchemy",
            "import requests",
            "import httpx",
            "import aiohttp",
            "import boto",
            "import redis",
            "import aiogram",
        ):
            self.assertNotIn(prohibited_import, source)


if __name__ == "__main__":
    unittest.main()
