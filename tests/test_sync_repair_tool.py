import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.sync_parity import build_table_parity_snapshot
from scripts import sync_repair_tool


class AsyncContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def fake_row():
    columns = [SimpleNamespace(name="id"), SimpleNamespace(name="offer_public_id"), SimpleNamespace(name="price")]
    return SimpleNamespace(__table__=SimpleNamespace(columns=columns), id=1, offer_public_id="ofr_1", price=100)


def write_snapshot(path: Path, rows: list[dict]) -> None:
    payload = {
        "status": "ok",
        "schema_version": 1,
        "mode": "quick",
        "tables": {"offers": build_table_parity_snapshot("offers", rows)},
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


class SyncRepairToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_row_dry_run_does_not_send(self):
        args = SimpleNamespace(
            table="offers",
            identity='{"offer_public_id":"ofr_1"}',
            operation="UPDATE",
            source_server="foreign",
            source_sequence=123,
            apply=False,
            confirm_write=False,
        )

        with patch("scripts.sync_repair_tool.AsyncSessionLocal", return_value=AsyncContext()), patch(
            "scripts.sync_repair_tool.load_row_by_identity", new=AsyncMock(return_value=fake_row())
        ), patch("scripts.sync_repair_tool._send_items") as send_items, redirect_stdout(StringIO()) as stdout:
            result = await sync_repair_tool.replay_row_command(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(result, 0)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["table"], "offers")
        send_items.assert_not_called()

    async def test_replay_row_apply_requires_confirm_and_source_sequence(self):
        base_args = dict(
            table="offers",
            identity='{"offer_public_id":"ofr_1"}',
            operation="UPDATE",
            source_server="foreign",
            target_server=None,
            target_url="https://peer.example",
            sync_api_key="secret",
        )

        with self.assertRaises(ValueError):
            await sync_repair_tool.replay_row_command(
                SimpleNamespace(**base_args, source_sequence=123, apply=True, confirm_write=False)
            )

        with self.assertRaises(ValueError):
            await sync_repair_tool.replay_row_command(
                SimpleNamespace(**base_args, source_sequence=None, apply=True, confirm_write=True)
            )

    def test_plan_command_outputs_dry_run_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.json"
            peer = Path(tmp) / "peer.json"
            write_snapshot(local, [{"id": 1, "offer_public_id": "ofr_1", "price": 100}])
            write_snapshot(peer, [{"id": 1, "offer_public_id": "ofr_1", "price": 101}])
            args = SimpleNamespace(
                local_snapshot=str(local),
                peer_snapshot=str(peer),
                direction="local-to-peer",
                sample_limit=5,
            )

            with redirect_stdout(StringIO()) as stdout:
                result = sync_repair_tool.plan_command(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["status"], "dry_run")
        self.assertEqual(payload["action_count"], 1)

    def test_watermark_command_outputs_redacted_payload(self):
        args = SimpleNamespace(
            source_server="foreign",
            aggregate_table="offers",
            aggregate_key="ofr_secret_1",
            source_sequence=123,
            payload_hash="abc123",
            operation="UPDATE",
            record_id="1",
        )

        with redirect_stdout(StringIO()) as stdout:
            result = sync_repair_tool.watermark_command(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["status"], "dry_run")
        self.assertNotIn("ofr_secret_1", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
