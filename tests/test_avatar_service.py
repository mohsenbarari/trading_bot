import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from core.services.avatar_service import resolve_owned_avatar_file_id
from models.chat_file import ChatFile


class AvatarServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_avatar_values_clear_avatar(self):
        db = SimpleNamespace(get=AsyncMock())

        self.assertIsNone(await resolve_owned_avatar_file_id(db, actor_id=5, avatar_file_id=None))
        self.assertIsNone(await resolve_owned_avatar_file_id(db, actor_id=5, avatar_file_id="   "))
        db.get.assert_not_called()

    async def test_rejects_missing_foreign_and_non_image_avatar_files(self):
        db = SimpleNamespace(get=AsyncMock(return_value=None))
        with self.assertRaises(HTTPException) as missing_exc:
            await resolve_owned_avatar_file_id(db, actor_id=5, avatar_file_id="avatar-1")
        self.assertEqual(missing_exc.exception.status_code, 404)

        db.get = AsyncMock(return_value=SimpleNamespace(id="avatar-2", uploader_id=9, mime_type="image/png"))
        with self.assertRaises(HTTPException) as foreign_exc:
            await resolve_owned_avatar_file_id(db, actor_id=5, avatar_file_id=" avatar-2 ")
        self.assertEqual(foreign_exc.exception.status_code, 403)

        db.get = AsyncMock(return_value=SimpleNamespace(id="avatar-3", uploader_id=5, mime_type="application/pdf"))
        with self.assertRaises(HTTPException) as type_exc:
            await resolve_owned_avatar_file_id(db, actor_id=5, avatar_file_id="avatar-3")
        self.assertEqual(type_exc.exception.status_code, 400)

    async def test_accepts_owned_image_avatar_file(self):
        db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id="avatar-ok", uploader_id=5, mime_type="image/webp")))

        self.assertEqual(await resolve_owned_avatar_file_id(db, actor_id=5, avatar_file_id=" avatar-ok "), "avatar-ok")
        db.get.assert_awaited_once_with(ChatFile, "avatar-ok")


if __name__ == "__main__":
    unittest.main()
