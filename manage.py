# manage.py (نسخه نهایی و اصلاح شده قطعی)
import sys
import os
from alembic.config import Config
from alembic import command

def main():
    """
    اسکریپت مایگریشن را با خواندن مستقیم متغیر محیطی اجرا می‌کند.
    """
    # خواندن آدرس دیتابیس از متغیرهای محیطی
    db_url = os.environ.get("SYNC_DATABASE_URL")
    if not db_url:
        print("!!! Error: SYNC_DATABASE_URL environment variable not set.")
        sys.exit(1)

    alembic_cfg = Config("alembic.ini")
    
    # تنظیم آدرس دیتابیس به صورت برنامه‌نویسی شده
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    
    print("--> Running database migrations to head...")
    try:
        command.upgrade(alembic_cfg, "head")
        print("--> Database migration completed successfully.")
    except Exception as e:
        print(f"!!! An error occurred during migration: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()