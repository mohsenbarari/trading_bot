import asyncio
from schemas import UserRead
from datetime import datetime

try:
    user_data = {
        "id": 101,
        "full_name": "Test User",
        "account_name": "testuser",
        "mobile_number": "09121234567",
        "telegram_id": None,
        "role": "عادی",
        "has_bot_access": False,
        "created_at": datetime.utcnow()
    }
    user = UserRead(**user_data)
    print("Schema parsing SUCCESS:")
    print(user.model_dump())
except Exception as e:
    print("Schema parsing ERROR:")
    print(e)
