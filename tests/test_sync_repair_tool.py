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


def replay_manifest(
    *,
    table: str = "offers",
    operation: str = "UPDATE",
    source_server: str = "foreign",
    source_sequence: int = 123,
    target_url: str = "https://peer.example",
    identity_fields: list[str] | None = None,
    identity_hash: str | None = None,
    git_branch: str = "candidate/sync-parity-hardening",
    git_commit: str = "abc123",
    approval: str = "apply-sync-repair:test",
) -> dict:
    return {
        "schema_version": 1,
        "type": "sync_repair_apply_manifest",
        "source_server": source_server,
        "target": {
            "target_server": "iran",
            "target_url_hash": sync_repair_tool._target_url_hash(target_url),
        },
        "table": table,
        "operation": operation,
        "identity_fields": identity_fields or ["offer_public_id"],
        "identity_hash": identity_hash or "placeholder",
        "expected_source_row_count": 1,
        "expected_target_row_count_impact": 1,
        "source_sequence": source_sequence,
        "before_parity_artifact_hash": "before-sha256",
        "after_parity_command": "python3 scripts/sync_parity_compare.py --after",
        "backup_artifact": "backup-manifest.json",
        "git_branch": git_branch,
        "git_commit": git_commit,
        "operator_approval_phrase": approval,
    }


def replay_identity_hash_for_fake_row() -> str:
    item = sync_repair_tool.build_current_state_replay_item(
        table_name="offers",
        row=fake_row(),
        operation="UPDATE",
        source_server="foreign",
        source_sequence=123,
    )
    return sync_repair_tool.summarize_replay_item(item)["record_parity"]["identity_hash"]


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

    async def test_replay_row_apply_requires_manifest_and_operator_approval(self):
        base_args = dict(
            table="offers",
            identity='{"offer_public_id":"ofr_1"}',
            operation="UPDATE",
            source_server="foreign",
            source_sequence=123,
            target_server="iran",
            target_url="https://peer.example",
            sync_api_key="secret",
            environment="staging",
            apply=True,
            confirm_write=True,
            allow_local_id_identity=False,
        )

        with self.assertRaises(ValueError):
            await sync_repair_tool.replay_row_command(
                SimpleNamespace(**base_args, manifest=None, operator_approval="approval")
            )

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(replay_manifest()), encoding="utf-8")
            with self.assertRaises(ValueError):
                await sync_repair_tool.replay_row_command(
                    SimpleNamespace(**base_args, manifest=str(manifest_path), operator_approval=None)
                )

    async def test_replay_row_apply_validates_manifest_before_sending(self):
        identity_hash = replay_identity_hash_for_fake_row()
        approval = f"apply-sync-repair:{identity_hash}"

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(
                json.dumps(replay_manifest(identity_hash=identity_hash, approval=approval)),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                table="offers",
                identity='{"offer_public_id":"ofr_1"}',
                operation="UPDATE",
                source_server="foreign",
                source_sequence=123,
                target_server="iran",
                target_url="https://peer.example",
                sync_api_key="secret",
                environment="staging",
                manifest=str(manifest_path),
                operator_approval=approval,
                allow_local_id_identity=False,
                apply=True,
                confirm_write=True,
            )

            with patch("scripts.sync_repair_tool.AsyncSessionLocal", return_value=AsyncContext()), patch(
                "scripts.sync_repair_tool.load_row_by_identity", new=AsyncMock(return_value=fake_row())
            ), patch(
                "scripts.sync_repair_tool._send_items",
                return_value={"status": "success", "errors": 0},
            ) as send_items, redirect_stdout(StringIO()) as stdout:
                result = await sync_repair_tool.replay_row_command(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(result, 0)
        self.assertFalse(payload["dry_run"])
        self.assertEqual(payload["target_url_hash"], sync_repair_tool._target_url_hash("https://peer.example"))
        send_items.assert_called_once()

    async def test_replay_row_apply_rejects_raw_local_id_identity_without_nonproduction_override(self):
        identity_hash = replay_identity_hash_for_fake_row()
        approval = f"apply-sync-repair:{identity_hash}"

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(
                json.dumps(replay_manifest(identity_fields=["id"], identity_hash=identity_hash, approval=approval)),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                table="offers",
                identity='{"id":1}',
                operation="UPDATE",
                source_server="foreign",
                source_sequence=123,
                target_server="iran",
                target_url="https://peer.example",
                sync_api_key="secret",
                environment="staging",
                manifest=str(manifest_path),
                operator_approval=approval,
                allow_local_id_identity=False,
                apply=True,
                confirm_write=True,
            )

            with patch("scripts.sync_repair_tool.AsyncSessionLocal", return_value=AsyncContext()), patch(
                "scripts.sync_repair_tool.load_row_by_identity", new=AsyncMock(return_value=fake_row())
            ), patch("scripts.sync_repair_tool._send_items") as send_items:
                with self.assertRaises(ValueError):
                    await sync_repair_tool.replay_row_command(args)

        send_items.assert_not_called()

    async def test_replay_row_apply_rejects_raw_local_id_identity_in_production_even_with_override(self):
        identity_hash = replay_identity_hash_for_fake_row()
        approval = f"apply-sync-repair:{identity_hash}"

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    replay_manifest(
                        identity_fields=["id"],
                        identity_hash=identity_hash,
                        git_branch="main",
                        approval=approval,
                    )
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                table="offers",
                identity='{"id":1}',
                operation="UPDATE",
                source_server="foreign",
                source_sequence=123,
                target_server="iran",
                target_url="https://peer.example",
                sync_api_key="secret",
                environment="production",
                manifest=str(manifest_path),
                operator_approval=approval,
                allow_local_id_identity=True,
                apply=True,
                confirm_write=True,
            )

            with patch("scripts.sync_repair_tool.AsyncSessionLocal", return_value=AsyncContext()), patch(
                "scripts.sync_repair_tool.load_row_by_identity", new=AsyncMock(return_value=fake_row())
            ), patch("scripts.sync_repair_tool._send_items") as send_items:
                with self.assertRaises(ValueError):
                    await sync_repair_tool.replay_row_command(args)

        send_items.assert_not_called()

    async def test_replay_row_apply_rejects_production_non_main_manifest(self):
        identity_hash = replay_identity_hash_for_fake_row()
        approval = f"apply-sync-repair:{identity_hash}"

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    replay_manifest(
                        identity_hash=identity_hash,
                        git_branch="candidate/sync-parity-hardening",
                        approval=approval,
                    )
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                table="offers",
                identity='{"offer_public_id":"ofr_1"}',
                operation="UPDATE",
                source_server="foreign",
                source_sequence=123,
                target_server="iran",
                target_url="https://peer.example",
                sync_api_key="secret",
                environment="production",
                manifest=str(manifest_path),
                operator_approval=approval,
                allow_local_id_identity=False,
                apply=True,
                confirm_write=True,
            )

            with patch("scripts.sync_repair_tool.AsyncSessionLocal", return_value=AsyncContext()), patch(
                "scripts.sync_repair_tool.load_row_by_identity", new=AsyncMock(return_value=fake_row())
            ), patch("scripts.sync_repair_tool._send_items") as send_items:
                with self.assertRaises(ValueError):
                    await sync_repair_tool.replay_row_command(args)

        send_items.assert_not_called()

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
        self.assertTrue(payload["apply_requires_manifest"])
        self.assertEqual(payload["repair_apply_manifest_template"]["type"], "sync_repair_apply_manifest")

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
