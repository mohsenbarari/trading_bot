import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.deps import EffectiveOwnerActor
from api.routers.trades import export_my_trades, export_trades_with_user


class FakeScalarRows:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class FakeExecuteResult:
    def __init__(self, values=None):
        self._values = values or []

    def scalars(self):
        return FakeScalarRows(self._values)


class FakeDB:
    def __init__(self, execute_results=None, users=None):
        self.execute_results = list(execute_results or [])
        self.users = users or {}
        self.executed_statements = []

    async def execute(self, stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        self.executed_statements.append(stmt)
        return self.execute_results.pop(0)

    async def get(self, model, user_id):
        return self.users.get(user_id)


class TradesRouterHistoryExportTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def make_context(owner_id=5, account_name="owner5"):
        owner_user = SimpleNamespace(id=owner_id, account_name=account_name, role=None)
        return EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=owner_user,
            relation=None,
            is_accountant_context=False,
        )

    async def test_export_my_trades_returns_excel_file_response(self):
        trade = SimpleNamespace(
            id=1,
            trade_number=10001,
            responder_user_id=5,
            trade_type="buy",
            commodity=SimpleNamespace(name="سکه"),
            quantity=2,
            price=123000,
            created_at=None,
        )
        db = FakeDB([FakeExecuteResult(values=[trade])])
        context = self.make_context()

        with patch(
            "api.routers.trades.build_trade_history_date_range_label",
            return_value="LABEL",
        ), patch(
            "api.routers.trades.generate_trade_history_excel_file",
            return_value="/tmp/history.xlsx",
        ) as generate_excel:
            response = await export_my_trades(
                format="excel",
                from_date=date(2026, 5, 1),
                to_date=date(2026, 5, 31),
                commodity_id=None,
                commodity_query=None,
                db=db,
                context=context,
            )

        self.assertEqual(response.path, "/tmp/history.xlsx")
        self.assertEqual(response.media_type, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("trade_history_owner5.xlsx", response.headers.get("content-disposition", ""))
        self.assertEqual(response.headers.get("cache-control"), "no-store, max-age=0")
        self.assertEqual(response.headers.get("pragma"), "no-cache")
        self.assertEqual(generate_excel.call_args.kwargs["subject_name"], "owner5")
        self.assertEqual(generate_excel.call_args.kwargs["date_range_label"], "LABEL")
        self.assertIn("ORDER BY trades.created_at ASC, trades.id ASC", str(db.executed_statements[0]))

    async def test_export_trades_with_user_uses_target_perspective_for_privileged_target_history(self):
        trade = SimpleNamespace(
            id=1,
            trade_number=10001,
            responder_user_id=77,
            trade_type="buy",
            commodity=SimpleNamespace(name="سکه"),
            quantity=2,
            price=123000,
            created_at=None,
        )
        db = FakeDB(
            [FakeExecuteResult(values=[trade])],
            users={77: SimpleNamespace(id=77, account_name="customer77")},
        )
        context = self.make_context(owner_id=5, account_name="owner5")
        relation = SimpleNamespace(owner_user_id=5)
        class FakeQuery:
            order_by_args = None

            def order_by(self, *args, **kwargs):
                self.order_by_args = args
                return self

        fake_query = FakeQuery()

        with patch(
            "api.routers.trades._build_trades_with_user_query",
            new=AsyncMock(return_value=(fake_query, relation)),
        ), patch(
            "api.routers.trades.build_trade_history_date_range_label",
            return_value="LABEL",
        ), patch(
            "api.routers.trades.build_trade_history_export_rows",
            return_value=["ROW"],
        ) as build_rows, patch(
            "api.routers.trades.generate_trade_history_pdf_file",
            return_value="/tmp/history.pdf",
        ):
            response = await export_trades_with_user(
                other_user_id=77,
                format="pdf",
                from_date=None,
                to_date=None,
                commodity_id=None,
                commodity_query=None,
                db=db,
                context=context,
            )

        self.assertEqual(response.path, "/tmp/history.pdf")
        self.assertEqual(response.media_type, "application/pdf")
        build_rows.assert_called_once_with([trade], 77)
        self.assertEqual([str(arg) for arg in fake_query.order_by_args], ["trades.created_at ASC", "trades.id ASC"])

    async def test_export_endpoints_reject_empty_results(self):
        context = self.make_context()

        with self.assertRaises(HTTPException) as exc_info:
            await export_my_trades(
                format="excel",
                from_date=None,
                to_date=None,
                commodity_id=None,
                commodity_query=None,
                db=FakeDB([FakeExecuteResult(values=[])]),
                context=context,
            )

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "معامله‌ای برای خروجی گرفتن یافت نشد.")


if __name__ == "__main__":
    unittest.main()
