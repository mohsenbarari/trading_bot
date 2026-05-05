import argparse
import asyncio
import sys
from pathlib import Path


current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent
sys.path.append(str(project_root))

from core.db import AsyncSessionLocal
from core.services.chat_backfill_service import backfill_direct_chats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill generic direct chats from legacy conversations.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the backfill. Without this flag the script runs in dry-run mode.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit how many conversations are inspected.",
    )
    parser.add_argument(
        "--conversation-id",
        type=int,
        default=None,
        help="Inspect or backfill one specific legacy conversation id.",
    )
    return parser


async def main() -> int:
    args = build_parser().parse_args()
    dry_run = not args.apply

    async with AsyncSessionLocal() as db:
        try:
            stats = await backfill_direct_chats(
                db,
                dry_run=dry_run,
                limit=args.limit,
                conversation_id=args.conversation_id,
            )

            if dry_run:
                await db.rollback()
                print("Dry run complete. No changes were written.")
            else:
                await db.commit()
                print("Backfill complete.")

            for key, value in stats.as_dict().items():
                print(f"{key}: {value}")
            return 0
        except Exception as exc:
            await db.rollback()
            print(f"Backfill failed: {exc}")
            return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))