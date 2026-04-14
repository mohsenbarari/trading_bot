import sys
import os
import asyncio

# Add the project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import select
from core.db import AsyncSessionLocal

from models.user import User
async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/free_deleted_user.py <user_id_or_mobile_or_account_name>")
        sys.exit(1)
        
    identifier = sys.argv[1]
    
    async with AsyncSessionLocal() as session:
        # Find user by ID, mobile, or username
        q = select(User)
        if identifier.isdigit() and len(identifier) < 10:
            q = q.where(User.id == int(identifier))
        else:
            q = q.where((User.mobile_number == identifier) | (User.account_name == identifier))
            
        res = await session.execute(q)
        user = res.scalar_one_or_none()
        
        if not user:
            print(f"❌ User '{identifier}' not found in the database.")
            sys.exit(1)
            
        if not user.is_deleted:
            print(f"⚠️ User '{user.account_name}' (ID: {user.id}) is NOT soft-deleted!")
            print("You must soft-delete them first using the Admin panel or Bot before freeing their namespace.")
            sys.exit(1)
            
        # Check if already freed
        if f"_del_{user.id}" in user.account_name:
            print(f"⚠️ User '{user.account_name}' (ID: {user.id}) has already been freed.")
            sys.exit(1)

        old_account = user.account_name
        old_mobile = user.mobile_number
            
        print(f"✅ Found Soft-Deleted User: {old_account} (ID: {user.id}) - Mobile: {old_mobile}")
        
        # Modify namespace
        user.account_name = f"{old_account}_del_{user.id}"
        user.mobile_number = f"{old_mobile}_del_{user.id}"
        user.telegram_id = None
        
        await session.commit()
        
        print(f"🎉 Successfully freed original namespace for ID {user.id}!")
        print(f"   -> Old Account: {old_account} | Old Mobile: {old_mobile}")
        print(f"   -> New Account: {user.account_name} | New Mobile: {user.mobile_number}")
        print(f"   -> Telegram ID cleared.")
        print("\nNote: Sync events have been automatically generated for the replica server.")

if __name__ == "__main__":
    asyncio.run(main())
