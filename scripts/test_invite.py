import asyncio
import sys
import os
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from core.db import AsyncSessionLocal
from models.user import User
from sqlalchemy import select
from core.security import create_access_token
import requests

async def test():
    async with AsyncSessionLocal() as db:
        stmt = select(User).where(User.mobile_number=="09370809280")
        user = (await db.execute(stmt)).scalar_one_or_none()
        token = create_access_token(str(user.id))
        
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.post("http://localhost:8000/api/invitations/", headers=headers, json={"account_name": "test2", "mobile_number": "09112223344", "role": "عادی"})
        print(resp.json())

if __name__ == "__main__":
    asyncio.run(test())
