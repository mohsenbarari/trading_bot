import sys
import os
import asyncio
from alembic.config import Config
from alembic import command
from sqlalchemy import select

# ایمپورت‌های مورد نیاز برای ساخت کاربر
from core.db import AsyncSessionLocal
from models.user import User
from core.enums import UserRole

# سعی می‌کنیم توابع نرمال‌سازی را ایمپورت کنیم، اگر نبودند ساده عمل می‌کنیم
try:
    from core.utils import normalize_account_name, normalize_persian_numerals
except ImportError:
    normalize_account_name = lambda x: x.lower().strip() if x else x
    normalize_persian_numerals = lambda x: x.strip() if x else x

def run_migrations():
    """
    اجرای عملیات مایگریشن دیتابیس (همگام)
    """
    db_url = os.environ.get("SYNC_DATABASE_URL")
    if not db_url:
        print("!!! Error: SYNC_DATABASE_URL environment variable not set.")
        sys.exit(1)

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    
    print("--> Running database migrations to head...")
    try:
        command.upgrade(alembic_cfg, "head")
        print("--> Database migration completed successfully.")
    except Exception as e:
        print(f"!!! An error occurred during migration: {e}")
        sys.exit(1)

async def create_super_admin_async():
    """
    منطق ایجاد کاربر ادمین ارشد (ناهمگام)
    """
    print("\n--- Create Super Admin ---")
    
    # 1. دریافت اطلاعات از ورودی
    full_name = input("Full Name: ").strip()
    if not full_name:
        print("Error: Name cannot be empty.")
        return

    account_name_raw = input("Account Name (English): ").strip()
    account_name = normalize_account_name(account_name_raw)
    if not account_name:
        print("Error: Account name cannot be empty.")
        return

    mobile_raw = input("Mobile Number (e.g., 09123456789): ").strip()
    mobile_number = normalize_persian_numerals(mobile_raw)
    if not mobile_number:
        print("Error: Mobile number cannot be empty.")
        return

    try:
        telegram_id_str = input("Telegram ID (Numeric): ").strip()
        telegram_id = int(telegram_id_str)
    except ValueError:
        print("Error: Telegram ID must be a number.")
        return

    # 2. اتصال به دیتابیس و بررسی تکراری بودن
    async with AsyncSessionLocal() as session:
        # بررسی نام کاربری
        stmt = select(User).where(User.account_name == account_name)
        if (await session.execute(stmt)).scalar_one_or_none():
            print(f"Error: Account name '{account_name}' already exists.")
            return

        # بررسی موبایل
        stmt = select(User).where(User.mobile_number == mobile_number)
        if (await session.execute(stmt)).scalar_one_or_none():
            print(f"Error: Mobile number '{mobile_number}' already exists.")
            return
            
        # بررسی تلگرام آیدی
        stmt = select(User).where(User.telegram_id == telegram_id)
        if (await session.execute(stmt)).scalar_one_or_none():
            print(f"Error: Telegram ID '{telegram_id}' already exists.")
            return

        # 3. ایجاد کاربر
        new_admin = User(
            full_name=full_name,
            account_name=account_name,
            mobile_number=mobile_number,
            telegram_id=telegram_id,
            role=UserRole.SUPER_ADMIN, # نقش ادمین ارشد
            has_bot_access=True,
            username=None # اختیاری
        )
        
        session.add(new_admin)
        await session.commit()
        print(f"\n✅ Super Admin '{full_name}' created successfully!")

def main():
    """
    تابع اصلی مدیریت دستورات
    """
    # بررسی آرگومان‌های خط فرمان
    if len(sys.argv) > 1:
        command_name = sys.argv[1]
        
        if command_name == "create_super_admin":
            try:
                asyncio.run(create_super_admin_async())
            except KeyboardInterrupt:
                print("\nOperation cancelled.")
            except Exception as e:
                print(f"!!! Error creating super admin: {e}")
            return
            
        # می‌توان دستورات دیگر را اینجا اضافه کرد
        
    # اگر هیچ دستوری وارد نشد یا دستور migrate بود، مایگریشن اجرا می‌شود
    # (این رفتار پیش‌فرض برای کانتینر migration ضروری است)
    run_migrations()

if __name__ == "__main__":
    main()