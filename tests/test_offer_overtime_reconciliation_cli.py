import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from scripts import report_offer_overtime_reconciliation as cli


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class OfferOvertimeReconciliationCliTests(unittest.IsolatedAsyncioTestCase):
    def _report(self, *, dry_run):
        return SimpleNamespace(
            dry_run=dry_run,
            finding_counts={},
            status_counts={},
            silent_owner_count=0,
            repaired=[],
            findings=[],
        )

    async def test_apply_registers_sync_events_before_authoritative_repair(self):
        session = SimpleNamespace(commit=AsyncMock())
        reconcile = AsyncMock(return_value=self._report(dry_run=False))
        setup_events = MagicMock()

        with (
            patch("core.db.AsyncSessionLocal", return_value=_SessionContext(session)),
            patch("core.events.setup_all_events", setup_events),
            patch(
                "core.services.offer_overtime_reconciliation_service.reconcile_overtime_requests",
                reconcile,
            ),
            patch("builtins.print"),
        ):
            result = await cli._main(dry_run=False, limit=7)

        self.assertEqual(result, 0)
        setup_events.assert_called_once_with()
        reconcile.assert_awaited_once_with(
            session,
            dry_run=False,
            limit=7,
            flush=True,
        )
        session.commit.assert_awaited_once_with()

    async def test_dry_run_does_not_register_mutation_listeners(self):
        session = SimpleNamespace(commit=AsyncMock())
        reconcile = AsyncMock(return_value=self._report(dry_run=True))
        setup_events = MagicMock()

        with (
            patch("core.db.AsyncSessionLocal", return_value=_SessionContext(session)),
            patch("core.events.setup_all_events", setup_events),
            patch(
                "core.services.offer_overtime_reconciliation_service.reconcile_overtime_requests",
                reconcile,
            ),
            patch("builtins.print"),
        ):
            result = await cli._main(dry_run=True, limit=5)

        self.assertEqual(result, 0)
        setup_events.assert_not_called()
        reconcile.assert_awaited_once_with(
            session,
            dry_run=True,
            limit=5,
            flush=False,
        )
        session.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
