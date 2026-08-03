#!/usr/bin/env python3
"""Safely publish the reviewed group-ingest subset to its immutable runtime copy.

The live group workers execute tiny copies under ``private-channel-ingest`` and
import the pinned ``runtime-source`` checkout.  This command keeps that
deliberate isolation while making a release explicit, auditable, backed up,
and atomic.  It never touches a database and defaults to a dry run.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_SOURCE = Path(
    "/srv/trading-bot-three-site-staging-data/coin-intelligence/runtime-source"
)
DEFAULT_PRIVATE_ROOT = Path(
    "/srv/trading-bot-three-site-staging-data/coin-intelligence/private-channel-ingest"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def release_files(runtime_source: Path, pipeline_root: Path) -> list[tuple[Path, Path]]:
    return [
        (
            REPOSITORY_ROOT / "core/market_intelligence/group_offer_parser.py",
            runtime_source / "core/market_intelligence/group_offer_parser.py",
        ),
        (
            REPOSITORY_ROOT / "core/market_intelligence/group_commodity_context.py",
            runtime_source / "core/market_intelligence/group_commodity_context.py",
        ),
        (
            REPOSITORY_ROOT / "core/market_intelligence/group_trade_parser.py",
            runtime_source / "core/market_intelligence/group_trade_parser.py",
        ),
        (
            REPOSITORY_ROOT
            / "scripts/coin_intelligence_private_ingest/run_rules_extractor.py",
            pipeline_root / "run_rules_extractor.py",
        ),
        (
            REPOSITORY_ROOT
            / "scripts/coin_intelligence_private_ingest/offer_field_extractor_v2.py",
            pipeline_root / "offer_field_extractor_v2.py",
        ),
        (
            REPOSITORY_ROOT
            / "scripts/coin_intelligence_private_ingest/link_group_trades_by_message_id.py",
            pipeline_root / "link_group_trades_by_message_id.py",
        ),
    ]


def validate(files: list[tuple[Path, Path]], runtime_source: Path, pipeline_root: Path) -> None:
    if not (runtime_source / "core").is_dir():
        raise RuntimeError(f"runtime source does not contain core/: {runtime_source}")
    if not pipeline_root.is_dir():
        raise RuntimeError(f"pipeline root does not exist: {pipeline_root}")
    for source, target in files:
        if not is_within(source, REPOSITORY_ROOT) or not source.is_file():
            raise RuntimeError(f"invalid candidate source: {source}")
        if not (is_within(target, runtime_source) or is_within(target, pipeline_root)):
            raise RuntimeError(f"refusing target outside approved roots: {target}")
        ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


def atomic_replace(source: Path, target: Path) -> None:
    payload = source.read_bytes()
    temp = target.with_name(f".{target.name}.candidate-{os.getpid()}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
        os.chmod(temp, mode)
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="perform the atomic release")
    parser.add_argument("--runtime-source", type=Path, default=DEFAULT_RUNTIME_SOURCE)
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    args = parser.parse_args()

    runtime_source = args.runtime_source.resolve()
    private_root = args.private_root.resolve()
    pipeline_root = private_root / "pipeline"
    files = release_files(runtime_source, pipeline_root)
    validate(files, runtime_source, pipeline_root)

    plan = [
        {
            "candidate": str(source.relative_to(REPOSITORY_ROOT)),
            "target": str(target),
            "candidate_sha256": digest(source),
            "target_sha256_before": digest(target) if target.exists() else None,
        }
        for source, target in files
    ]
    if not args.apply:
        print(json.dumps({"status": "DRY_RUN", "files": plan}, ensure_ascii=False, indent=2))
        return

    backup_root = private_root / "runtime-backups"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_root / stamp
    backup.mkdir(mode=0o700)
    for source, target in files:
        relative = (
            Path("runtime-source") / target.relative_to(runtime_source)
            if is_within(target, runtime_source)
            else Path("pipeline") / target.relative_to(pipeline_root)
        )
        backup_target = backup / relative
        backup_target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(target, backup_target)
        atomic_replace(source, target)
        if digest(target) != digest(source):
            raise RuntimeError(f"post-release digest mismatch: {target}")

    manifest = {
        "released_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backup": str(backup),
        "files": [
            {
                **entry,
                "target_sha256_after": digest(target),
            }
            for entry, (_, target) in zip(plan, files, strict=True)
        ],
    }
    (backup / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "RELEASED", **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
