#!/usr/bin/env python3
"""Validate and atomically record a supervised coin-group review batch.

The decision pack contains only opaque event keys and normalized economic
fields.  Raw Telegram text and identities are forbidden.  The canonical
Market Store is opened read-only and every decision must still match the
event being reviewed before the feedback sidecar can change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.coin_group_feedback import (  # noqa: E402
    CoinGroupFeedbackError,
    record_coin_group_parser_feedback_batch,
)


SCHEMA = "coin-group-supervised-review-batch/1.0"
CONFIRMATION = "apply-supervised-coin-group-review-batch"
FORBIDDEN_KEY_PARTS = frozenset(
    {"raw", "text", "message", "sender", "telegram", "user", "phone", "name"}
)


class ReviewBatchError(RuntimeError):
    """Payload-free refusal reason."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_pack(path: Path, expected_sha256: str) -> dict[str, Any]:
    if not path.is_file() or _sha256(path) != expected_sha256.lower():
        raise ReviewBatchError("review_batch_digest_mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ReviewBatchError("review_batch_schema_invalid")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ReviewBatchError("review_batch_decisions_missing")
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ReviewBatchError("review_batch_decision_invalid")
        for key in decision:
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                raise ReviewBatchError("review_batch_private_field_forbidden")
    return payload


def _open_market(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve(strict=True)}?mode=ro", uri=True, timeout=30
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    check = connection.execute("PRAGMA quick_check").fetchone()
    if check is None or str(check[0]).lower() != "ok":
        connection.close()
        raise ReviewBatchError("review_batch_market_check_failed")
    return connection


def _attributes(row: sqlite3.Row) -> dict[str, object]:
    try:
        value = json.loads(str(row["attributes_json"] or "{}"))
    except (TypeError, ValueError) as exc:
        raise ReviewBatchError("review_batch_market_attributes_invalid") from exc
    return value if isinstance(value, dict) else {}


def _validate_against_market(
    market: sqlite3.Connection, decisions: Sequence[Mapping[str, object]]
) -> None:
    seen: set[bytes] = set()
    for decision in decisions:
        try:
            key = bytes.fromhex(str(decision.get("event_key") or ""))
        except ValueError as exc:
            raise ReviewBatchError("review_batch_event_key_invalid") from exc
        if not 16 <= len(key) <= 64 or key in seen:
            raise ReviewBatchError("review_batch_event_key_invalid")
        seen.add(key)
        row = market.execute(
            """
            SELECT source_code,event_time_utc,event_type,side,price_num,
                   quantity_num,settlement_term,trade_form,is_conditional,
                   attributes_json
            FROM market_observations WHERE event_key=?
            """,
            (key,),
        ).fetchone()
        if row is None:
            raise ReviewBatchError("review_batch_event_not_found")
        source = str(row["source_code"] or "")
        attributes = _attributes(row)
        try:
            group_number = int(source[-1])
        except (ValueError, IndexError) as exc:
            raise ReviewBatchError("review_batch_source_invalid") from exc
        expected = (
            str(decision.get("event_type") or "").upper(),
            int(decision.get("group_number") or 0),
            str(decision.get("source_event_time_utc") or ""),
            str(decision.get("side") or "").upper(),
            int(decision.get("price_project_thousand_toman") or 0),
            int(decision.get("quantity") or 0),
            str(decision.get("settlement_term") or "").upper(),
            str(decision.get("trade_form") or "").upper(),
            bool(decision.get("is_conditional")),
        )
        actual = (
            str(row["event_type"] or "").upper(),
            group_number,
            str(row["event_time_utc"] or ""),
            str(row["side"] or "").upper(),
            int(float(row["price_num"])),
            int(float(row["quantity_num"] or 1)),
            str(row["settlement_term"] or "").upper(),
            str(row["trade_form"] or "").upper(),
            bool(row["is_conditional"]),
        )
        if (
            expected != actual
            or int(attributes.get("group_number") or group_number) != group_number
        ):
            raise ReviewBatchError("review_batch_event_drift")


def _online_backup(source: Path, destination: Path) -> None:
    if destination.exists():
        raise ReviewBatchError("review_batch_backup_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(
        f"file:{source.resolve(strict=True)}?mode=ro", uri=True, timeout=30
    )
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
        check = destination_connection.execute("PRAGMA quick_check").fetchone()
        if check is None or str(check[0]).lower() != "ok":
            raise ReviewBatchError("review_batch_backup_check_failed")
    finally:
        destination_connection.close()
        source_connection.close()
    destination.chmod(0o600)


def run(args: argparse.Namespace) -> dict[str, object]:
    payload = _load_pack(args.batch, args.expected_sha256)
    decisions = payload["decisions"]
    market = _open_market(args.market_store)
    try:
        _validate_against_market(market, decisions)
    finally:
        market.close()
    if not args.apply:
        return {
            "status": "VALIDATED",
            "decisions": len(decisions),
            "batch_sha256": args.expected_sha256.lower(),
        }
    if args.confirm != CONFIRMATION or args.backup is None:
        raise ReviewBatchError("review_batch_apply_confirmation_required")
    _online_backup(args.feedback_store, args.backup)
    result = record_coin_group_parser_feedback_batch(
        args.feedback_store,
        decisions,
        reviewer=args.reviewer,
        reviewed_at_utc=str(payload.get("reviewed_at_utc") or ""),
    )
    check = sqlite3.connect(
        f"file:{args.feedback_store.resolve()}?mode=ro", uri=True
    )
    try:
        verified = check.execute("PRAGMA quick_check").fetchone()
    finally:
        check.close()
    if verified is None or str(verified[0]).lower() != "ok":
        raise ReviewBatchError("review_batch_feedback_check_failed")
    return {
        "status": "RECORDED_PENDING_PIPELINE",
        **result,
        "batch_sha256": args.expected_sha256.lower(),
        "backup_sha256": _sha256(args.backup),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--market-store", type=Path, required=True)
    parser.add_argument("--feedback-store", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--backup", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = run(build_parser().parse_args(argv))
    except (ReviewBatchError, CoinGroupFeedbackError, OSError, sqlite3.Error, ValueError):
        print(json.dumps({"status": "FAILED", "reason": "REVIEW_BATCH_FAILED"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
