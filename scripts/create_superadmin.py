import os
import sys
import asyncio
from pathlib import Path

# Add project root to sys.path so we can import from core/models
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
sys.path.append(str(project_root))

from core.db import AsyncSessionLocal, init_db
from models.user import User, UserRole
from sqlalchemy import select
from core.security import get_password_hash

async def create_superadmin(mobile: str, account_name: str, temp_password: str):
    await init_db()
    async with AsyncSessionLocal() as db:
        # Check if SUPER_ADMIN already exists
        stmt = select(User).where(User.role == UserRole.SUPER_ADMIN)
        existing_admin = (await db.execute(stmt)).scalar_one_or_none()
        
        if existing_admin:
            print(f"❌ Error: A SUPER_ADMIN already exists! Account: {existing_admin.account_name}")
            return
            
        print(f"🔧 Creating Super Admin: {account_name} ({mobile})")
        
        admin_user = User(
            account_name=account_name,
            mobile_number=mobile,
            role=UserRole.SUPER_ADMIN,
            full_name=account_name,
            address="System Default",
            has_bot_access=False,
            telegram_id=None,
            must_change_password=True,
            admin_password_hash=get_password_hash(temp_password)
        )
        
        db.add(admin_user)
        try:
            await db.commit()
            print("✅ Super Admin successfully created!")
            print(f"You can now login with:\nMobile: {mobile}\nOTP: (via SMS, and then must enter the temporary password to set a new one)")
        except Exception as e:
            await db.rollback()
            print(f"❌ Failed to create super admin: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python create_superadmin.py <mobile_number> <account_name> <temporary_password>")
        sys.exit(1)
        
    mobile_input = sys.argv[1]
    name_input = sys.argv[2]
    password_input = sys.argv[3]
    
    asyncio.run(create_superadmin(mobile_input, name_input, password_input))
