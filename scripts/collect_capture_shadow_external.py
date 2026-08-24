#!/usr/bin/env python3
"""Collect the independent Wallex input directly into a canonical Market Store."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Iterator, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ESTIMATOR_ROOT = REPO_ROOT / "apps" / "coin_rate_estimator"
if str(ESTIMATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ESTIMATOR_ROOT))

from telegram_price_collector.external_collectors import (  # noqa: E402
    ExternalSourceError,
    fetch_wallex_live,
)
from core.market_intelligence.external_markets import (  # noqa: E402
    ExternalQuoteInput,
    usdt_toman_quote_to_observation,
)
from core.market_intelligence.market_contracts import normalize_utc  # noqa: E402
from core.market_intelligence.market_store import (  # noqa: E402
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)


COMMAND_VERSION = "capture-shadow-external-v1"


class ExternalCommandError(RuntimeError):
    pass


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExternalCommandError("capture_shadow_external_in_progress") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _runtime(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if root.is_symlink() or not root.is_dir():
        raise ExternalCommandError("runtime_root_unavailable")
    try:
        root.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return root
    raise ExternalCommandError("runtime_root_inside_repository")


def _inside(root: Path, value: str, *, field: str) -> Path:
    supplied = Path(value).expanduser()
    path = supplied.resolve() if supplied.is_absolute() else (root / supplied).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ExternalCommandError(f"{field}_outside_runtime_root") from exc
    if path == root or path.is_symlink():
        raise ExternalCommandError(f"{field}_invalid")
    return path


def _run(args: argparse.Namespace) -> int:
    root = _runtime(args.runtime_root)
    market_path = _inside(root, args.market_store, field="market_store")
    lock_path = _inside(root, args.lock_file, field="lock_file")
    for parent in (market_path.parent, lock_path.parent):
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(parent, 0o700)
    available = normalize_utc(datetime.now(timezone.utc), field_name="external_receipt_utc")
    with _lock(lock_path):
        rows = fetch_wallex_live()
        if not rows:
            raise ExternalCommandError("wallex_empty_snapshot")
        connection = connect_market_store(market_path)
        try:
            initialize_market_store(connection)
            written = 0
            for row in rows:
                if row.source != "WALLEX_PUBLIC_API" or row.instrument != "USDT_IRT":
                    continue
                observed = normalize_utc(row.observed_at_utc, field_name="wallex_observed_at_utc")
                observation = usdt_toman_quote_to_observation(
                    ExternalQuoteInput(
                        source_code="WALLEX_PUBLIC_API",
                        source_event_id=f"{observed}:{row.quote_kind}",
                        observed_at_utc=observed,
                        available_at_utc=max(observed, available),
                        quote_kind=row.quote_kind,  # type: ignore[arg-type]
                        price=row.normalized_price,
                    )
                )
                upsert_observation(connection, observation)
                written += 1
            if written <= 0:
                raise ExternalCommandError("wallex_no_supported_observations")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
    print(
        json.dumps(
            {
                "command": "collect-external",
                "version": COMMAND_VERSION,
                "status": "COLLECTED",
                "observations": written,
                "available_at_utc": available,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--market-store", default="market/market.sqlite3")
    parser.add_argument("--lock-file", default="run/external.lock")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    previous_umask = os.umask(0o077)
    try:
        return _run(build_parser().parse_args(argv))
    except (ExternalCommandError, ExternalSourceError, OSError, sqlite3.Error, ValueError) as exc:
        print(
            json.dumps(
                {
                    "command": "collect-external",
                    "version": COMMAND_VERSION,
                    "status": "FAILED",
                    "reason": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
