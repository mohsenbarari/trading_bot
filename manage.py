# trading_bot/manage.py (نسخه نهایی و مستقل)
import os
from alembic.config import Config
from alembic import command

def main():
    """
    اسکریپت مایگریشن را با خواندن مستقیم متغیر محیطی اجرا می‌کند.
    """
    alembic_cfg = Config("alembic.ini")
    
    # خواندن مستقیم متغیر محیطی برای جلوگیری از تداخل
    db_url = os.environ.get("SYNC_DATABASE_URL")
    if not db_url:
        raise ValueError("SYNC_DATABASE_URL environment variable not set!")
        
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    
    # اجرای دستور upgrade head
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception as e:
        print(f"An error occurred during migration: {e}")
        # خروج با کد خطا تا Docker Compose متوجه شکست شود
        exit(1)

if __name__ == "__main__":
    main()