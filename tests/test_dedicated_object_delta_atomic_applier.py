from __future__ import annotations

from dataclasses import dataclass
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

    async def apply_db_change(self, change: _Change) -> None:
        self.calls.append(f"apply:{change.identity}")
        if self.fail_on == change.identity:
            raise RuntimeError(f"apply failed: {change.identity}")

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


def _apply_plan(*, changes: tuple[_Change, ...] | None = None) -> _Plan:
    return _Plan(
        action=IMPORT_ACTION_APPLY,
        changes_to_apply=changes
        if changes is not None
        else (_Change(1, "one"), _Change(2, "two")),
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
        replay = _Plan(
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
                plan=_apply_plan(changes=(_Change(2, "first"), _Change(4, "skipped"))),
                adapter=adapter,
            )

        self.assertEqual(0, adapter.begin_count)
        self.assertEqual([], transaction.calls)

    async def test_later_contiguous_batch_is_accepted_without_requiring_genesis_sequence(self) -> None:
        transaction = _Transaction()

        result = await apply_atomic_object_delta_plan(
            plan=_apply_plan(changes=(_Change(41, "later-one"), _Change(42, "later-two"))),
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
