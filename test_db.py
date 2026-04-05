import asyncio
from sqlalchemy.future import select
from core.db import AsyncSessionLocal
from models.user import User
from models.session import UserSession

async def main():
    async with AsyncSessionLocal() as session:
        user = (await session.execute(select(User).where(User.mobile_number == '09370809280'))).scalar_one_or_none()
        if not user:
            print("No user")
            # Try getting any user
            users = (await session.execute(select(User))).scalars().all()
            for u in users:
                print(f"User: {u.mobile_number}")
            return
        print(f"Found user {user.mobile_number}")
        sessions = (await session.execute(select(UserSession).where(UserSession.user_id == user.id))).scalars().all()
        if not sessions:
            print("No sessions found")
        for s in sessions:
            print(f"Session {s.id}: is_primary={s.is_primary}, is_active={s.is_active}")

asyncio.run(main())
