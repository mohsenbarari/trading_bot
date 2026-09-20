"""Characterization tests for get_chat_file success path.

Seed: API-MSG-GET-CHAT-GET-CHAT-FILE-L2773 (api.routers.chat.get_chat_file).
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md.
Assignment: WA-P1-00B-FREEZE-20260904-001.

Stage lifecycle state: P1-00 COMPLETE / P1-00b IN_PROGRESS / BLOCKED_EXPLICIT.

Evidence limits:
This test characterizes current success behavior for the get_chat_file endpoint
strictly at handler boundaries as a recovery-mandated singleton. Scope ends at
patched requester-loading and authorization helper boundaries, an AsyncMock database
session, a real TemporaryDirectory file, and real FastAPI FileResponse construction.
Underlying database queries, Redis pub/sub delivery, network transports, and FastAPI
dependency injection mechanics are mocked or isolated rather than executed or claimed.
This module records current baseline behavior without claiming seed closure, durable
rollback, Stage acceptance, or tests already run.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.chat import get_chat_file
from fastapi.responses import FileResponse
from models.chat_file import ChatFile

fence = install_p1_00_network_fence()


class ChatGetFileSuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_chat_file_authorized_existing_path_returns_exact_file_response(self):
        token = "characterization-test-token-valid-abc123"
        file_id = "c46-file-uuid-1234-5678-9abc"
        requester_id = 101
        requester = SimpleNamespace(id=requester_id)

        file_content = b"%PDF-1.4 characterization payload for get_chat_file test\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "characterization_document.pdf")
            with open(file_path, "wb") as f:
                f.write(file_content)

            self.assertTrue(os.path.exists(file_path))

            chat_file = ChatFile(
                id=file_id,
                uploader_id=202,
                s3_key=file_path,
                file_name="characterization_document.pdf",
                mime_type="application/pdf",
                size=len(file_content),
            )

            awaited_calls: list[str] = []

            async def fake_load_requester(*args, **kwargs):
                awaited_calls.append("_load_chat_file_requester")
                return requester

            async def fake_db_get(*args, **kwargs):
                awaited_calls.append("db.get")
                return chat_file

            async def fake_authorize(*args, **kwargs):
                awaited_calls.append("_authorize_chat_file_access")
                return None

            load_requester_mock = AsyncMock(side_effect=fake_load_requester)
            db = AsyncMock()
            db.get = AsyncMock(side_effect=fake_db_get)
            authorize_mock = AsyncMock(side_effect=fake_authorize)

            with patch(
                "api.routers.chat._load_chat_file_requester",
                new=load_requester_mock,
            ), patch(
                "api.routers.chat._authorize_chat_file_access",
                new=authorize_mock,
            ):
                denied_before = len(fence["denied_connects"])
                redis_before = len(fence["live_redis_constructs"])

                response = await get_chat_file(
                    file_id=file_id,
                    db=db,
                    token=token,
                )

                self.assertEqual(len(fence["denied_connects"]), denied_before)
                self.assertEqual(len(fence["live_redis_constructs"]), redis_before)

            self.assertEqual(
                awaited_calls,
                [
                    "_load_chat_file_requester",
                    "db.get",
                    "_authorize_chat_file_access",
                ],
            )

            self.assertEqual(load_requester_mock.call_count, 1)
            self.assertEqual(load_requester_mock.await_count, 1)
            load_requester_mock.assert_called_once_with(db, token)
            load_requester_mock.assert_awaited_once_with(db, token)
            self.assertIs(load_requester_mock.call_args.args[0], db)
            self.assertEqual(load_requester_mock.call_args.args[1], token)
            self.assertIs(load_requester_mock.await_args.args[0], db)
            self.assertEqual(load_requester_mock.await_args.args[1], token)

            self.assertEqual(db.get.call_count, 1)
            self.assertEqual(db.get.await_count, 1)
            db.get.assert_called_once_with(ChatFile, file_id)
            db.get.assert_awaited_once_with(ChatFile, file_id)
            self.assertIs(db.get.call_args.args[0], ChatFile)
            self.assertEqual(db.get.call_args.args[1], file_id)
            self.assertIs(db.get.await_args.args[0], ChatFile)
            self.assertEqual(db.get.await_args.args[1], file_id)

            self.assertEqual(authorize_mock.call_count, 1)
            self.assertEqual(authorize_mock.await_count, 1)
            authorize_mock.assert_called_once_with(
                db,
                chat_file=chat_file,
                file_id=file_id,
                requester_id=requester.id,
            )
            authorize_mock.assert_awaited_once_with(
                db,
                chat_file=chat_file,
                file_id=file_id,
                requester_id=requester.id,
            )
            self.assertIs(authorize_mock.call_args.args[0], db)
            self.assertIs(authorize_mock.call_args.kwargs["chat_file"], chat_file)
            self.assertEqual(authorize_mock.call_args.kwargs["file_id"], file_id)
            self.assertEqual(authorize_mock.call_args.kwargs["requester_id"], requester.id)
            self.assertIs(authorize_mock.await_args.args[0], db)
            self.assertIs(authorize_mock.await_args.kwargs["chat_file"], chat_file)
            self.assertEqual(authorize_mock.await_args.kwargs["file_id"], file_id)
            self.assertEqual(authorize_mock.await_args.kwargs["requester_id"], requester.id)

            self.assertIs(type(response), FileResponse)
            self.assertIsInstance(response, FileResponse)
            self.assertEqual(response.path, chat_file.s3_key)
            self.assertEqual(response.path, file_path)
            self.assertEqual(response.media_type, chat_file.mime_type)
            self.assertEqual(response.filename, chat_file.file_name)

            self.assertTrue(os.path.exists(file_path))
            self.assertTrue(os.path.isfile(file_path))
            with open(file_path, "rb") as f:
                disk_bytes = f.read()
            self.assertEqual(disk_bytes, file_content)


if __name__ == "__main__":
    unittest.main()
