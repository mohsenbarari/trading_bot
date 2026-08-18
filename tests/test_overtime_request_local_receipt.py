"""Local requester-message receipts must not mutate synced lifecycle state."""

import unittest

from sqlalchemy.dialects import postgresql

from bot.overtime_request_status import _persist_requester_status_receipt_locally
from models.offer_request import OfferRequest


class FakeSession:
    def __init__(self):
        self.execute_calls = []
        self.flush_called = False

    async def execute(self, statement, execution_options=None):
        self.execute_calls.append((statement, execution_options))

    async def flush(self):
        self.flush_called = True


class OvertimeRequesterReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_receipt_update_preserves_lifecycle_version(self):
        session = FakeSession()
        ledger = OfferRequest(
            id=17,
            version_id=2,
            requester_status_outbox_id=None,
        )

        await _persist_requester_status_receipt_locally(session, ledger, 81)

        self.assertEqual(ledger.requester_status_outbox_id, 81)
        self.assertEqual(ledger.version_id, 2)
        self.assertFalse(session.flush_called)
        self.assertEqual(len(session.execute_calls), 1)
        statement, execution_options = session.execute_calls[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("UPDATE offer_requests", str(compiled))
        self.assertIn("requester_status_outbox_id", str(compiled))
        self.assertNotIn("version_id =", str(compiled))
        self.assertEqual(execution_options, {"is_sync": True})

    async def test_unpersisted_proxy_does_not_write(self):
        session = FakeSession()
        ledger = OfferRequest(version_id=1, requester_status_outbox_id=None)

        await _persist_requester_status_receipt_locally(session, ledger, 81)

        self.assertEqual(session.execute_calls, [])
        self.assertIsNone(ledger.requester_status_outbox_id)


if __name__ == "__main__":
    unittest.main()
