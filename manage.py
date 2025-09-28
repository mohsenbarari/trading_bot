# manage.py
import argparse
from alembic.config import Config
from alembic import command
import os

def main():
    alembic_cfg = Config("alembic.ini")
    sync_db_url = os.getenv("SYNC_DATABASE_URL")
    if not sync_db_url:
        raise ValueError("SYNC_DATABASE_URL environment variable is not set for Alembic.")
    alembic_cfg.set_main_option('sqlalchemy.url', sync_db_url)

    parser = argparse.ArgumentParser(description="Run Alembic database migrations.")
    subparsers = parser.add_subparsers(dest="action", required=True, help="Alembic command")
    
    rev_parser = subparsers.add_parser("revision", help="Create a new revision file.")
    rev_parser.add_argument("--autogenerate", action="store_true", help="Autogenerate revision.")
    rev_parser.add_argument("-m", "--message", type=str, required=True, help="Revision message.")
    
    up_parser = subparsers.add_parser("upgrade", help="Upgrade to a revision.")
    up_parser.add_argument("revision", type=str, default="head", nargs="?", help="Revision ID.")
    
    subparsers.add_parser("current", help="Display the current revision.")

    args = parser.parse_args()
    
    try:
        if args.action == "revision":
            command.revision(alembic_cfg, message=args.message, autogenerate=args.autogenerate)
        elif args.action == "upgrade":
            command.upgrade(alembic_cfg, args.revision)
        elif args.action == "current":
            command.current(alembic_cfg)
        print(f"Alembic command '{args.action}' executed successfully.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()