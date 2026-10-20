"""Three new direct-handler trade-read prequery failure characterizations.

Prospective manual unit757; an unapplied Codex-authored proposal, not acceptance.
Seeds: API-TRADE-GET-TRADES-GET-MY-TRADES-L5462,
API-TRADE-GET-TRADES-EXPORT-MY-TRADES-L5566, and
API-TRADE-GET-TRADES-GET-TRADE-L5626.

Only synthetic exception identity, exact visible argument forwarding and
call/await suppression are established. These are not HTTP/Depends/authentication,
real SQL/query/transaction/persistence, full-row immutability, export/file delivery,
concurrency/retry/reload, canonical seed credit, origin or Stage-closure proofs.
No current755 proposal, result, reservation or fixture is reused.
"""

from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers import trades
from models.trade import Trade


class TradeReadPrequeryFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Call each real selected handler, failing at its first query-builder boundary."""

    def _fixture(self) -> SimpleNamespace:
        owner = SimpleNamespace(id=75701, account_name="synthetic owner")
        actor = SimpleNamespace(id=75702)
        context = SimpleNamespace(owner_user=owner, actor_user=actor)
        filters = {
            "from_date": date(2026, 10, 8),
            "to_date": date(2026, 10, 19),
            "commodity_id": 17,
            "commodity_query": " preserved commodity ",
            "settlement_type": "tomorrow",
            "trade_type": "sell",
        }
        db_async = {
            name: AsyncMock(
                name=f"db.{name}",
                side_effect=AssertionError(f"unreached db.{name}"),
            )
            for name in ("execute", "commit", "delete", "flush", "refresh", "rollback")
        }
        db_sync = {
            name: Mock(
                name=f"db.{name}",
                side_effect=AssertionError(f"unreached db.{name}"),
            )
            for name in ("add", "add_all")
        }
        # Standard visible boundaries, not a custom Session or SQLite adapter.
        db = SimpleNamespace(**db_async, **db_sync)
        async_names = (
            "_serialize_trade_history_rows",
            "_build_viewer_scoped_trade_history_export_rows",
            "_viewer_can_access_trade_history_row",
            "_load_trade_identity_map",
            "_load_trade_customer_relation_map_for_user_ids",
        )
        sync_names = (
            "_build_my_trades_query",
            "_build_trade_history_select",
            "select",
            "selectinload",
            "_partition_trade_customer_relations_for_viewer",
            "trade_to_response",
            "_build_trade_history_export_subject_name",
            "build_trade_history_date_range_label",
            "build_trade_history_export_rows",
            "generate_trade_history_excel_file",
            "generate_trade_history_pdf_file",
            "_build_trade_history_download_name",
            "_build_trade_history_file_response",
            "FileResponse",
        )
        controls = {
            name: AsyncMock(
                name=name, side_effect=AssertionError(f"unreached {name}")
            )
            for name in async_names
        }
        controls.update(
            {
                name: Mock(
                    name=name, side_effect=AssertionError(f"unreached {name}")
                )
                for name in sync_names
            }
        )
        return SimpleNamespace(
            owner=owner,
            actor=actor,
            context=context,
            ids_before=(owner.id, actor.id),
            filters=filters,
            filters_before=dict(filters),
            db=db,
            db_async=db_async,
            db_sync=db_sync,
            controls=controls,
            async_names=async_names,
            originals={name: getattr(trades, name) for name in controls},
            handlers={
                name: getattr(trades, name)
                for name in ("get_my_trades", "export_my_trades", "get_trade")
            },
        )

    def _assert_unreached(self, fixture: SimpleNamespace, *, boundary: str) -> None:
        for name, control in fixture.db_async.items():
            self.assertEqual(control.call_count, 0, f"db.{name} calls")
            self.assertEqual(control.await_count, 0, f"db.{name} awaits")
        for name, control in fixture.db_sync.items():
            control.assert_not_called()
        for name, control in fixture.controls.items():
            if name == boundary:
                continue
            self.assertEqual(control.call_count, 0, name)
            if name in fixture.async_names:
                self.assertEqual(control.await_count, 0, name)
        # Exactly these fixture fields only; no full-row or durable-state claim.
        self.assertEqual((fixture.owner.id, fixture.actor.id), fixture.ids_before)
        self.assertEqual(fixture.filters, fixture.filters_before)

    def _assert_restored(self, fixture: SimpleNamespace) -> None:
        for name, original in fixture.originals.items():
            self.assertIs(getattr(trades, name), original, name)
        for name, original in fixture.handlers.items():
            self.assertIs(getattr(trades, name), original, name)

    def _assert_exact_query_forwarding(
        self, fixture: SimpleNamespace, builder: Mock
    ) -> None:
        builder.assert_called_once_with(
            fixture.owner.id,
            from_date=fixture.filters["from_date"],
            to_date=fixture.filters["to_date"],
            commodity_id=fixture.filters["commodity_id"],
            commodity_query=fixture.filters["commodity_query"],
            settlement_type=fixture.filters["settlement_type"],
            perspective_trade_type=fixture.filters["trade_type"],
        )
        self.assertNotEqual(fixture.owner.id, fixture.actor.id)
        self.assertEqual(builder.call_args.args, (fixture.owner.id,))
        self.assertEqual(len(builder.call_args.kwargs), 6)

    async def test_get_my_trades_query_builder_exception_precedes_execute(self) -> None:
        """Real handler forwards owner/all filters, then preserves builder failure."""
        fixture = self._fixture()
        failure = RuntimeError("757 synthetic my-trades initial query-builder failure")
        builder = Mock(name="_build_my_trades_query", side_effect=failure)
        fixture.controls["_build_my_trades_query"] = builder
        handler = fixture.handlers["get_my_trades"]

        with patch.multiple(trades, **fixture.controls):
            self.assertIs(trades.get_my_trades, handler)
            with self.assertRaises(RuntimeError) as raised:
                await handler(
                    skip=4,
                    limit=9,
                    db=fixture.db,
                    context=fixture.context,
                    **fixture.filters,
                )
            self.assertIs(raised.exception, failure)
            self._assert_exact_query_forwarding(fixture, builder)
            self._assert_unreached(fixture, boundary="_build_my_trades_query")

        self._assert_restored(fixture)

    async def test_export_my_trades_query_builder_exception_precedes_execute_and_export(
        self,
    ) -> None:
        """No execute, row export, generator or response work follows builder failure."""
        fixture = self._fixture()
        failure = RuntimeError("757 synthetic my-export initial query-builder failure")
        builder = Mock(name="_build_my_trades_query", side_effect=failure)
        fixture.controls["_build_my_trades_query"] = builder
        handler = fixture.handlers["export_my_trades"]

        with patch.multiple(trades, **fixture.controls):
            self.assertIs(trades.export_my_trades, handler)
            with self.assertRaises(RuntimeError) as raised:
                await handler(
                    format="excel",
                    db=fixture.db,
                    context=fixture.context,
                    **fixture.filters,
                )
            self.assertIs(raised.exception, failure)
            self._assert_exact_query_forwarding(fixture, builder)
            self._assert_unreached(fixture, boundary="_build_my_trades_query")

        self._assert_restored(fixture)

    async def test_get_trade_select_exception_precedes_loading_and_execute(self) -> None:
        """Initial select(Trade) failure suppresses loading/access/serialization."""
        fixture = self._fixture()
        failure = RuntimeError("757 synthetic trade-detail initial select failure")
        select_builder = Mock(name="select", side_effect=failure)
        fixture.controls["select"] = select_builder
        handler = fixture.handlers["get_trade"]

        with patch.multiple(trades, **fixture.controls):
            self.assertIs(trades.get_trade, handler)
            self.assertIs(trades.Trade, Trade)
            with self.assertRaises(RuntimeError) as raised:
                await handler(
                    trade_id=75703,
                    db=fixture.db,
                    context=fixture.context,
                )
            self.assertIs(raised.exception, failure)
            select_builder.assert_called_once_with(Trade)
            self._assert_unreached(fixture, boundary="select")

        self._assert_restored(fixture)


if __name__ == "__main__":
    unittest.main()
