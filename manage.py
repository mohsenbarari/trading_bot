# manage.py (نسخه نهایی و کامل)
import argparse
import os
from alembic.config import Config
from alembic import command
from core.config import settings

def main():
    # ساخت یک کانفیگ Alembic به صورت برنامه‌نویسی
    alembic_cfg = Config("alembic.ini")
    
    # تنظیم آدرس دیتابیس به صورت دستی تا Alembic سردرگم نشود
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)

    parser = argparse.ArgumentParser(description="Manage database migrations.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    # دستور revision
    parser_revision = subparsers.add_parser("revision", help="Create a new revision file.")
    parser_revision.add_argument("--autogenerate", action="store_true", help="Autogenerate revision from models.")
    parser_revision.add_argument("-m", "--message", type=str, help="Revision message.")

    # دستور upgrade
    parser_upgrade = subparsers.add_parser("upgrade", help="Upgrade to a later version.")
    parser_upgrade.add_argument("revision", type=str, nargs="?", default="head", help="The revision to upgrade to.")

    # --- دستور جدید downgrade ---
    parser_downgrade = subparsers.add_parser("downgrade", help="Revert to a previous version.")
    parser_downgrade.add_argument("revision", type=str, nargs="?", help="The revision to downgrade to (e.g., -1, base).")
    
    # دستور current
    parser_current = subparsers.add_parser("current", help="Display the current revision.")

    args = parser.parse_args()
    action = args.action

    try:
        if action == "revision":
            command.revision(alembic_cfg, message=args.message, autogenerate=args.autogenerate)
        elif action == "upgrade":
            command.upgrade(alembic_cfg, args.revision)
        # --- منطق جدید برای downgrade ---
        elif action == "downgrade":
            if not args.revision:
                print("Error: downgrade command requires a revision (e.g., -1 or a specific hash).")
                return
            command.downgrade(alembic_cfg, args.revision)
        elif action == "current":
            command.current(alembic_cfg)
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()