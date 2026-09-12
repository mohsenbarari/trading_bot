import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.chat import get_chat_file
from models.chat_file import ChatFile


class LoadRequesterSentinelError(Exception):
    """Sentinel error raised when requester lookup fails."""


class DbGetSentinelError(Exception):
    """Sentinel error raised when database lookup fails."""


class AuthorizeAccessSentinelError(Exception):
    """Sentinel error raised when authorization fails."""


class TestChatGetFileFailureBoundaries(unittest.IsolatedAsyncioTestCase):
    async def test_get_chat_file_load_requester_failure_boundary(self):
        token = "sentinel-token-load-requester-failure"
        file_id = "file-boundary-1"
        sentinel_error = LoadRequesterSentinelError("failed loading requester")

        db = AsyncMock()
        db.get = AsyncMock()

        with patch(
            "api.routers.chat._load_chat_file_requester",
            new=AsyncMock(side_effect=sentinel_error),
        ) as mock_load_requester, patch(
            "api.routers.chat._authorize_chat_file_access",
            new=AsyncMock(),
        ) as mock_authorize, patch(
            "api.routers.chat.os.path.exists",
        ) as mock_exists, patch(
            "fastapi.responses.FileResponse",
        ) as mock_file_response:
            with self.assertRaises(LoadRequesterSentinelError) as cm:
                await get_chat_file(file_id=file_id, db=db, token=token)

            self.assertIs(cm.exception, sentinel_error)
            mock_load_requester.assert_awaited_once_with(db, token)
            db.get.assert_not_called()
            self.assertEqual(db.get.await_count, 0)
            self.assertEqual(db.get.call_count, 0)
            mock_authorize.assert_not_called()
            self.assertEqual(mock_authorize.await_count, 0)
            self.assertEqual(mock_authorize.call_count, 0)
            mock_exists.assert_not_called()
            mock_file_response.assert_not_called()

    async def test_get_chat_file_db_get_failure_boundary(self):
        token = "sentinel-token-db-get-failure"
        file_id = "file-boundary-2"
        mock_requester = SimpleNamespace(id=42)
        sentinel_error = DbGetSentinelError("failed db.get ChatFile lookup")

        db = AsyncMock()
        db.get = AsyncMock(side_effect=sentinel_error)

        with patch(
            "api.routers.chat._load_chat_file_requester",
            new=AsyncMock(return_value=mock_requester),
        ) as mock_load_requester, patch(
            "api.routers.chat._authorize_chat_file_access",
            new=AsyncMock(),
        ) as mock_authorize, patch(
            "api.routers.chat.os.path.exists",
        ) as mock_exists, patch(
            "fastapi.responses.FileResponse",
        ) as mock_file_response:
            with self.assertRaises(DbGetSentinelError) as cm:
                await get_chat_file(file_id=file_id, db=db, token=token)

            self.assertIs(cm.exception, sentinel_error)
            mock_load_requester.assert_awaited_once_with(db, token)
            db.get.assert_awaited_once_with(ChatFile, file_id)
            mock_authorize.assert_not_called()
            self.assertEqual(mock_authorize.await_count, 0)
            self.assertEqual(mock_authorize.call_count, 0)
            mock_exists.assert_not_called()
            mock_file_response.assert_not_called()

    async def test_get_chat_file_authorize_access_failure_boundary(self):
        token = "sentinel-token-authorize-access-failure"
        file_id = "file-boundary-3"
        mock_requester = SimpleNamespace(id=99)
        mock_chat_file = SimpleNamespace(
            id=file_id,
            s3_key="/tmp/boundary3.bin",
            mime_type="application/octet-stream",
            file_name="boundary3.bin",
            uploader_id=101,
        )
        sentinel_error = AuthorizeAccessSentinelError("failed authorizing file access")

        db = AsyncMock()
        db.get = AsyncMock(return_value=mock_chat_file)

        with patch(
            "api.routers.chat._load_chat_file_requester",
            new=AsyncMock(return_value=mock_requester),
        ) as mock_load_requester, patch(
            "api.routers.chat._authorize_chat_file_access",
            new=AsyncMock(side_effect=sentinel_error),
        ) as mock_authorize, patch(
            "api.routers.chat.os.path.exists",
        ) as mock_exists, patch(
            "fastapi.responses.FileResponse",
        ) as mock_file_response:
            with self.assertRaises(AuthorizeAccessSentinelError) as cm:
                await get_chat_file(file_id=file_id, db=db, token=token)

            self.assertIs(cm.exception, sentinel_error)
            mock_load_requester.assert_awaited_once_with(db, token)
            db.get.assert_awaited_once_with(ChatFile, file_id)
            mock_authorize.assert_awaited_once_with(
                db,
                chat_file=mock_chat_file,
                file_id=file_id,
                requester_id=mock_requester.id,
            )
            mock_exists.assert_not_called()
            mock_file_response.assert_not_called()


if __name__ == "__main__":
    unittest.main()
