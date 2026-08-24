#!/usr/bin/env python3
"""Build a privacy-minimized dependency seed for capture-input shadowing.

The new Telegram spools replace public/group/private-offer inputs.  A remote
shadow still needs independent public-API history, causal pre-cutover facts,
and learned parser-calibration receipts.  This command copies only normalized
Market Store rows; it never copies raw staging data.  Retaining verified
private trades after cutover is an explicit transition-only option because the
new capture contract does not claim that an edit is a trade.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.market_store import (  # noqa: E402
    connect_market_store,
    initialize_market_store,
)
from core.market_intelligence.market_contracts import normalize_utc  # noqa: E402


COMMAND_VERSION = "capture-shadow-seed-v1"
_COLUMNS = (
    "event_key",
    "source_code",
    "source_family",
    "event_time_utc",
    "available_at_utc",
    "tehran_datetime",
    "tehran_date",
    "tehran_minute",
    "tehran_weekday",
    "instrument",
    "market_label",
    "settlement_term",
    "trade_form",
    "event_type",
    "side",
    "price_value",
    "price_num",
    "price_unit",
    "currency",
    "quantity_value",
    "quantity_num",
    "quantity_unit",
    "parse_confidence",
    "parser_version",
    "quality_state",
    "quality_policy_version",
    "is_conditional",
    "attributes_json",
    "inserted_at_utc",
)


class ShadowSeedError(RuntimeError):
    pass


def _emit(**payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def _external_file(value: str, *, field: str, must_exist: bool) -> Path:
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ShadowSeedError(f"{field}_inside_repository")
    if path.is_symlink() or (must_exist and not path.is_file()):
        raise ShadowSeedError(f"{field}_unavailable")
    if not must_exist and path.exists():
        raise ShadowSeedError(f"{field}_already_exists")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ShadowSeedError(f"{field}_parent_unavailable")
    return path


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _run(args: argparse.Namespace) -> int:
    source_path = _external_file(args.source_market_store, field="source_market_store", must_exist=True)
    output_path = _external_file(args.output_market_store, field="output_market_store", must_exist=False)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    destination = connect_market_store(output_path)
    try:
        market_cutover = normalize_utc(
            args.market_cutover_utc,
            field_name="capture_shadow_market_cutover_utc",
        )
        group_cutover = normalize_utc(
            args.group_cutover_utc,
            field_name="capture_shadow_group_cutover_utc",
        )
        initialize_market_store(destination)
        source_version = source.execute(
            "SELECT schema_version,contract_version FROM market_store_metadata WHERE singleton=1"
        ).fetchone()
        destination_version = destination.execute(
            "SELECT schema_version,contract_version FROM market_store_metadata WHERE singleton=1"
        ).fetchone()
        if source_version is None or tuple(source_version) != tuple(destination_version):
            raise ShadowSeedError("market_store_contract_mismatch")
        placeholders = ",".join("?" for _ in _COLUMNS)
        insert = (
            f"INSERT OR IGNORE INTO market_observations({','.join(_COLUMNS)}) "
            f"VALUES({placeholders})"
        )
        private_gold_clause = (
            "(available_at_utc < ? OR event_type='TRADE')"
            if args.retain_post_cutover_private_trades
            else "available_at_utc < ?"
        )
        selected = source.execute(
            f"""
            SELECT {','.join(_COLUMNS)}
            FROM market_observations
            WHERE source_family='EXTERNAL_MARKET'
               OR (
                    source_code='PRIVATE_GOLD_CHANNEL'
                    AND quality_state='ELIGIBLE'
                    AND {private_gold_clause}
               )
               OR (
                    source_code='PRIVATE_GOLD_PAPER_MINUTE'
                    AND quality_state='ELIGIBLE'
                    AND available_at_utc < ?
               )
               OR (
                    source_code IN ('MELTED_AGGREGATE','MELTED_FLOW','USD_HERAT','XAUUSD')
                    AND quality_state='ELIGIBLE'
                    AND available_at_utc < ?
               )
               OR (
                    source_code IN ('GROUP_1','GROUP_2')
                    AND (
                        (quality_state='ELIGIBLE' AND available_at_utc < ?)
                        OR json_extract(attributes_json,'$.human_feedback_syntax_fingerprint') IS NOT NULL
                    )
               )
            ORDER BY event_time_utc,event_key
            """,
            (market_cutover, market_cutover, market_cutover, group_cutover),
        )
        counters = {
            "external_facts": 0,
            "pre_cutover_market_facts": 0,
            "pre_cutover_group_facts": 0,
            "pre_cutover_private_trades": 0,
            "retained_post_cutover_private_trades": 0,
            "parser_calibration_receipts": 0,
        }
        batch: list[tuple[object, ...]] = []
        for row in selected:
            family = str(row["source_family"])
            source_code = str(row["source_code"])
            if family == "EXTERNAL_MARKET":
                counters["external_facts"] += 1
            elif source_code == "PRIVATE_GOLD_CHANNEL" and str(row["event_type"]) == "TRADE":
                if str(row["available_at_utc"]) >= market_cutover:
                    counters["retained_post_cutover_private_trades"] += 1
                else:
                    counters["pre_cutover_private_trades"] += 1
            elif source_code in {"GROUP_1", "GROUP_2"} and json.loads(
                str(row["attributes_json"] or "{}")
            ).get("human_feedback_syntax_fingerprint"):
                counters["parser_calibration_receipts"] += 1
            elif source_code in {"GROUP_1", "GROUP_2"}:
                counters["pre_cutover_group_facts"] += 1
            else:
                counters["pre_cutover_market_facts"] += 1
            batch.append(tuple(row[column] for column in _COLUMNS))
            if len(batch) >= 5_000:
                destination.executemany(insert, batch)
                batch.clear()
        if batch:
            destination.executemany(insert, batch)
        destination.commit()
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ShadowSeedError("shadow_seed_integrity_failed")
    except BaseException:
        destination.rollback()
        destination.close()
        source.close()
        if output_path.exists():
            output_path.unlink()
        raise
    destination.close()
    source.close()
    _emit(
        command="build-seed",
        version=COMMAND_VERSION,
        status="BUILT",
        sha256=_digest(output_path),
        bytes=output_path.stat().st_size,
        **counters,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-market-store", required=True)
    parser.add_argument("--output-market-store", required=True)
    parser.add_argument("--market-cutover-utc", required=True)
    parser.add_argument("--group-cutover-utc", required=True)
    parser.add_argument(
        "--retain-post-cutover-private-trades",
        action="store_true",
        help="transition-only: retain verified private trades after market cutover",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except (ShadowSeedError, OSError, sqlite3.Error) as exc:
        _emit(command="build-seed", version=COMMAND_VERSION, status="FAILED", reason=str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
