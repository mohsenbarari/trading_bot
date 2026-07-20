#!/usr/bin/env python3
"""Guarded one-time conversion of legacy ChatFile paths to immutable blobs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import stat

from sqlalchemy import func, select

from core.config import settings
from core.db import AsyncSessionLocal
from core.dr_blob_plane import bind_chat_file_blob
from core.runtime_identity import resolve_runtime_identity
from core.webapp_writer_control import load_writer_snapshot
from core.writer_fencing import writer_fence_scope
from models.chat_file import ChatFile


MAX_LEGACY_FILE_BYTES = 50 * 1024 * 1024
CONFIRMATION = "BACKFILL-LEGACY-CHAT-FILES"


def _read_legacy_file(path_text: str, expected_size: int) -> bytes:
    path = Path(path_text)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError("legacy chat file is not a stable single-link regular file")
        if metadata.st_size != expected_size or metadata.st_size > MAX_LEGACY_FILE_BYTES:
            raise RuntimeError("legacy chat file size conflicts with its database row")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError("legacy chat file ended before its recorded size")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise RuntimeError("legacy chat file grew during stable read")
        after = os.fstat(fd)
        if (
            after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
        ):
            raise RuntimeError("legacy chat file changed during stable read")
        return b"".join(chunks)
    finally:
        os.close(fd)


async def run(*, apply: bool, limit: int) -> dict:
    identity = resolve_runtime_identity(settings)
    async with AsyncSessionLocal() as session:
        total = int(
            await session.scalar(
                select(func.count()).select_from(ChatFile).where(ChatFile.content_hash.is_(None))
            )
            or 0
        )
        rows = (
            await session.execute(
                select(ChatFile)
                .where(ChatFile.content_hash.is_(None))
                .order_by(ChatFile.created_at, ChatFile.id)
                .limit(max(1, min(1000, int(limit))))
            )
        ).scalars().all()
        preview = [
            {
                "id_hash": hashlib.sha256(str(row.id).encode("utf-8")).hexdigest(),
                "size": int(row.size),
            }
            for row in rows
        ]
        if not apply:
            return {"status": "dry-run", "remaining": total, "batch": preview}
        snapshot = await load_writer_snapshot(session)

    with writer_fence_scope(
        identity,
        snapshot,
        source="legacy_chat_file_backfill",
        require_witness_lease=bool(settings.writer_witness_required),
    ):
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(ChatFile)
                    .where(ChatFile.content_hash.is_(None))
                    .order_by(ChatFile.created_at, ChatFile.id)
                    .with_for_update(skip_locked=True)
                    .limit(max(1, min(1000, int(limit))))
                )
            ).scalars().all()
            for row in rows:
                contents = _read_legacy_file(str(row.s3_key), int(row.size))
                await bind_chat_file_blob(session, chat_file=row, contents=contents)
            await session.commit()
            return {"status": "applied", "converted": len(rows), "remaining_before": total}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.apply and args.confirm != CONFIRMATION:
        parser.error(f"--apply requires --confirm {CONFIRMATION}")
    try:
        result = asyncio.run(run(apply=args.apply, limit=args.limit))
    except Exception as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
