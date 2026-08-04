#!/usr/bin/env python3
"""Explicit local collector for allowlisted public coin-market Telegram feeds.

It is intentionally a one-shot command: no cron, worker, app hook, or API
route invokes it. Credentials come only from process environment, while the
session and Market Store must reside under one pre-existing protected root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Sequence

from core.market_intelligence.public_telegram.sources import (
    PUBLIC_TELEGRAM_SOURCES,
    source_for_code,
)
from core.market_intelligence.public_telegram.transport import (
    PublicTelegramCredentials,
    PublicTelegramTransportError,
    PublicTelegramTransportSettings,
    collect_public_market_telegram,
)


class PublicTelegramCommandError(RuntimeError):
    """The explicit collector command is not safe to run as requested."""


def _emit(**payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def _runtime_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise PublicTelegramCommandError("runtime_root_unavailable")
    return root


def _path_inside_root(root: Path, value: str, *, field_name: str) -> Path:
    supplied = Path(value).expanduser()
    candidate = supplied.resolve() if supplied.is_absolute() else (root / supplied).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PublicTelegramCommandError(f"{field_name}_outside_runtime_root") from exc
    if candidate == root:
        raise PublicTelegramCommandError(f"{field_name}_must_be_file")
    return candidate


def _selected_sources(values: Sequence[str] | None):
    if not values:
        return PUBLIC_TELEGRAM_SOURCES
    return tuple(source_for_code(value) for value in values)


def _collect(args: argparse.Namespace) -> int:
    root = _runtime_root(args.runtime_root)
    store_path = _path_inside_root(root, args.market_store, field_name="market_store")
    session_path = _path_inside_root(root, args.session, field_name="telegram_session")
    if not store_path.parent.is_dir():
        raise PublicTelegramCommandError("market_store_parent_unavailable")
    if not session_path.parent.is_dir():
        raise PublicTelegramCommandError("telegram_session_parent_unavailable")
    if args.bootstrap_session and not sys.stdin.isatty():
        raise PublicTelegramCommandError("interactive_session_requires_tty")
    results = asyncio.run(
        collect_public_market_telegram(
            PublicTelegramTransportSettings(
                credentials=PublicTelegramCredentials.from_environment(),
                market_store_path=store_path,
                session_path=session_path,
                allow_interactive_login=bool(args.bootstrap_session),
            ),
            sources=_selected_sources(args.source),
            days=int(args.days),
            resume_from_checkpoint=not bool(args.replay_window),
            batch_size=int(args.batch_size),
            request_wait_seconds=args.request_wait_seconds,
        )
    )
    _emit(command="collect", status="COLLECTED", sources=results)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--market-store", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--request-wait-seconds", type=float, default=None)
    parser.add_argument("--source", action="append", choices=[source.code for source in PUBLIC_TELEGRAM_SOURCES])
    parser.add_argument(
        "--replay-window",
        action="store_true",
        help="ignore checkpoints and read the requested bounded history window",
    )
    parser.add_argument(
        "--bootstrap-session",
        action="store_true",
        help="allow one interactive login only from a TTY",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _collect(args)
    except (PublicTelegramCommandError, PublicTelegramTransportError, ValueError) as exc:
        _emit(command="collect", status="FAILED", reason=str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
