import hashlib
import hmac
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from api.routers.sync import resync_from_changelog


class FakeExecuteResult:
    def __init__(self, values):
        self._values = list(values)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commits = 0

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def commit(self):
        self.commits += 1


class FakeAsyncClient:
    def __init__(self, response=None, error=None, calls=None, **kwargs):
        self.response = response
        self.error = error
        self.calls = calls if calls is not None else []
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, content, headers):
        self.calls.append((url, content, headers))
        if self.error:
            raise self.error
        return self.response


def make_entry(entry_id, **overrides):
    data = {
        "id": entry_id,
        "operation": "INSERT",
        "table_name": "users",
        "record_id": entry_id,
        "data": {"full_name": f"User {entry_id}"},
        "hash": f"hash-{entry_id}",
        "timestamp": datetime(2026, 1, 1, 12, 0, 0),
        "synced": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class SyncRouterResyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_resync_requires_dev_key_and_sync_configuration(self):
        request = SimpleNamespace(headers={})

        with patch("api.routers.sync.settings.dev_api_key", "dev-key"):
            with self.assertRaises(HTTPException) as exc_info:
                await resync_from_changelog(request=request, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 403)

        request = SimpleNamespace(headers={"X-Dev-Api-Key": "dev-key"})
        with patch("api.routers.sync.settings.dev_api_key", "dev-key"), patch(
            "api.routers.sync.default_peer_server_url", return_value=None
        ), patch("api.routers.sync.settings.sync_api_key", "secret"):
            with self.assertRaises(HTTPException) as exc_info:
                await resync_from_changelog(request=request, db=FakeDB())
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "Sync not configured (peer server URL or SYNC_API_KEY missing)")

    async def test_resync_returns_early_when_no_unsynced_entries(self):
        request = SimpleNamespace(headers={"X-Dev-Api-Key": "dev-key"})
        db = FakeDB([FakeExecuteResult([])])

        with patch("api.routers.sync.settings.dev_api_key", "dev-key"), patch(
            "api.routers.sync.default_peer_server_url", return_value="https://peer.example"
        ), patch("api.routers.sync.settings.sync_api_key", "secret"):
            result = await resync_from_changelog(request=request, db=db)

        self.assertEqual(result, {"status": "ok", "message": "No unsynced entries found", "processed": 0})
        self.assertEqual(db.commits, 0)

    async def test_resync_marks_entries_synced_and_signs_payload(self):
        request = SimpleNamespace(headers={"X-Dev-Api-Key": "dev-key"})
        entry = make_entry(1)
        db = FakeDB([FakeExecuteResult([entry])])
        calls = []
        client_factory = lambda **kwargs: FakeAsyncClient(
            response=SimpleNamespace(status_code=200, text="ok"),
            calls=calls,
            **kwargs,
        )

        with patch("api.routers.sync.settings.dev_api_key", "dev-key"), patch(
            "api.routers.sync.default_peer_server_url", return_value="https://peer.example/"
        ), patch("api.routers.sync.settings.sync_api_key", "secret"), patch(
            "api.routers.sync.time.time", return_value=1_700_000_111
        ), patch("httpx.AsyncClient", side_effect=client_factory):
            result = await resync_from_changelog(request=request, db=db)

        self.assertEqual(result, {"status": "ok", "processed": 1, "errors": 0, "total_entries": 1})
        self.assertTrue(entry.synced)
        self.assertEqual(db.commits, 1)
        self.assertEqual(len(calls), 1)
        url, content, headers = calls[0]
        self.assertEqual(url, "https://peer.example/api/sync/receive")

        expected_items = [
            {
                "type": "db_change",
                "operation": "INSERT",
                "table": "users",
                "id": 1,
                "data": {"full_name": "User 1"},
                "hash": "hash-1",
                "timestamp": datetime(2026, 1, 1, 12, 0, 0).timestamp(),
            }
        ]
        expected_body = json.dumps(expected_items, sort_keys=True, default=str)
        expected_signature = hmac.new(
            b"secret",
            f"1700000111:{expected_body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(content, expected_body)
        self.assertEqual(headers["X-API-Key"], "secret")
        self.assertEqual(headers["X-Timestamp"], "1700000111")
        self.assertEqual(headers["X-Signature"], expected_signature)

    async def test_resync_counts_parse_and_transport_errors(self):
        request = SimpleNamespace(headers={"X-Dev-Api-Key": "dev-key"})
        good = make_entry(1)
        bad = make_entry(2, data="not-json")
        db = FakeDB([FakeExecuteResult([bad, good])])
        client_factory = lambda **kwargs: FakeAsyncClient(error=RuntimeError("network down"), **kwargs)

        with patch("api.routers.sync.settings.dev_api_key", "dev-key"), patch(
            "api.routers.sync.default_peer_server_url", return_value="https://peer.example"
        ), patch("api.routers.sync.settings.sync_api_key", "secret"), patch(
            "httpx.AsyncClient", side_effect=client_factory
        ):
            result = await resync_from_changelog(request=request, db=db)

        self.assertEqual(result, {"status": "ok", "processed": 0, "errors": 3, "total_entries": 2})
        self.assertFalse(good.synced)
        self.assertEqual(db.commits, 1)

    async def test_resync_preserves_accountant_relation_and_actor_fields_in_payload(self):
        request = SimpleNamespace(headers={"X-Dev-Api-Key": "dev-key"})
        relation_entry = make_entry(
            4,
            table_name="accountant_relations",
            record_id=41,
            data={
                "owner_user_id": 9,
                "accountant_user_id": 11,
                "created_by_user_id": 9,
                "relation_display_name": "دفتر تهران",
                "status": "active",
            },
        )
        offer_entry = make_entry(
            5,
            table_name="offers",
            record_id=77,
            data={
                "user_id": 9,
                "actor_user_id": 11,
                "price": 125000,
                "quantity": 3,
            },
        )
        trade_entry = make_entry(
            6,
            table_name="trades",
            record_id=88,
            data={
                "offer_id": 77,
                "offer_user_id": 9,
                "responder_user_id": 12,
                "actor_user_id": 11,
                "quantity": 1,
                "price": 125000,
            },
        )
        db = FakeDB([FakeExecuteResult([relation_entry, offer_entry, trade_entry])])
        calls = []
        client_factory = lambda **kwargs: FakeAsyncClient(
            response=SimpleNamespace(status_code=200, text="ok"),
            calls=calls,
            **kwargs,
        )

        with patch("api.routers.sync.settings.dev_api_key", "dev-key"), patch(
            "api.routers.sync.default_peer_server_url", return_value="https://peer.example"
        ), patch("api.routers.sync.settings.sync_api_key", "secret"), patch(
            "api.routers.sync.time.time", return_value=1_700_000_222
        ), patch("httpx.AsyncClient", side_effect=client_factory):
            result = await resync_from_changelog(request=request, db=db)

        self.assertEqual(result, {"status": "ok", "processed": 3, "errors": 0, "total_entries": 3})
        self.assertTrue(relation_entry.synced)
        self.assertTrue(offer_entry.synced)
        self.assertTrue(trade_entry.synced)
        self.assertEqual(len(calls), 1)
        _, content, _ = calls[0]
        payload = json.loads(content)
        self.assertEqual(payload[0]["table"], "accountant_relations")
        self.assertEqual(payload[0]["data"]["relation_display_name"], "دفتر تهران")
        self.assertEqual(payload[1]["table"], "offers")
        self.assertEqual(payload[1]["data"]["actor_user_id"], 11)
        self.assertEqual(payload[2]["table"], "trades")
        self.assertEqual(payload[2]["data"]["actor_user_id"], 11)


if __name__ == "__main__":
    unittest.main()