from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from models.chat_file import ChatFile


async def resolve_owned_avatar_file_id(
    db: AsyncSession,
    *,
    actor_id: int,
    avatar_file_id: str | None,
) -> str | None:
    if avatar_file_id is None:
        return None

    normalized_id = avatar_file_id.strip()
    if not normalized_id:
        return None

    avatar_file = await db.get(ChatFile, normalized_id)
    if not avatar_file:
        raise HTTPException(status_code=404, detail="فایل آواتار پیدا نشد")
    if avatar_file.uploader_id != actor_id:
        raise HTTPException(status_code=403, detail="فقط می‌توانید از تصویر آپلودشده توسط خودتان استفاده کنید")
    if not (avatar_file.mime_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="آواتار باید فایل تصویری باشد")

    return avatar_file.id