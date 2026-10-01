"""Hermetic characterization test for trade export self-delegation.

Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-091
Blocker ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed API: API-TRADE-GET-TRADES-EXPORT-TRADES-WITH-USER-L5797

Scenario:
Exercises live api.routers.trades.export_trades_with_user with other_user_id
equal to context.owner_user.id (self-target scenario), proving immediate delegation
to export_my_trades with exact forwarding of all supported export/filter arguments,
the database session sentinel, and context instance, returning the exact delegated
response without accessing the database or pair-history export helpers.

Stated Exclusions:
Excludes non-self pair export, generated file bytes, successful query/serializer
paths, product changes, inventory reconciliation, seed/blocker closure, origin
cells, and Stage closure.
"""

from __future__ import annotations

import os
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.deps import EffectiveOwnerActor
from api.routers.trades import export_trades_with_user


class _NoMethodDBSentinel:
    """Sentinel database session object with no database methods."""

    def __init__(self) -> None:
        self.access_count = 0

    def __getattr__(self, name: str):
        self.access_count += 1
        raise AssertionError(
            f"Unexpected database session attribute or method access: db.{name}"
        )


class TradeExportSelfDelegationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for export_trades_with_user self-target delegation."""

    async def test_self_target_delegates_exact_filters_and_returns_export_response(
        self,
    ) -> None:
        owner_id = 42
        owner_user = SimpleNamespace(
            id=owner_id,
            account_name="test_owner_user_42",
            role=None,
        )
        context = EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=owner_user,
            relation=None,
            is_accountant_context=False,
        )
        db_sentinel = _NoMethodDBSentinel()
        response_sentinel = SimpleNamespace(name="delegated_export_response_sentinel")

        explicit_format = "excel"
        from_date = date(2026, 7, 1)
        to_date = date(2026, 7, 15)
        commodity_id = 9
        commodity_query = "gold_bar"
        settlement_type = "cash"
        trade_type = "buy"

        with patch(
            "api.routers.trades.export_my_trades",
            new=AsyncMock(return_value=response_sentinel),
        ) as mock_export_my_trades:
            response = await export_trades_with_user(
                other_user_id=context.owner_user.id,
                format=explicit_format,
                from_date=from_date,
                to_date=to_date,
                commodity_id=commodity_id,
                commodity_query=commodity_query,
                settlement_type=settlement_type,
                trade_type=trade_type,
                db=db_sentinel,
                context=context,
            )

        mock_export_my_trades.assert_awaited_once_with(
            format=explicit_format,
            from_date=from_date,
            to_date=to_date,
            commodity_id=commodity_id,
            commodity_query=commodity_query,
            settlement_type=settlement_type,
            trade_type=trade_type,
            db=db_sentinel,
            context=context,
        )
        self.assertIs(response, response_sentinel)
        self.assertIs(mock_export_my_trades.call_args.kwargs["db"], db_sentinel)
        self.assertIs(mock_export_my_trades.call_args.kwargs["context"], context)
        self.assertEqual(db_sentinel.access_count, 0)


if __name__ == "__main__":
    unittest.main()
