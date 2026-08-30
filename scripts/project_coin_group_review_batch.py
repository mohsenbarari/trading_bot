#!/usr/bin/env python3
"""Project a recorded exact-event review batch after transient text expires.

This is a bounded repair path, not a parser bypass.  Every opaque key must
already exist in both the privacy-safe feedback sidecar and Market Store, and
the complete normalized event must still match the pinned decision pack.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.coin_group_feedback import (  # noqa: E402
    CoinGroupFeedbackError,
    load_coin_group_parser_feedback,
    mark_coin_group_parser_feedback_applied,
)
from core.market_intelligence.coin_group_review_projection import (  # noqa: E402
    CoinGroupReviewProjectionError,
    project_coin_group_reviews,
    reconcile_pending_trades_from_reviewed_roots,
)
from core.market_intelligence.market_store import connect_market_store  # noqa: E402
from scripts.apply_coin_group_review_batch import (  # noqa: E402
    ReviewBatchError,
    _load_pack,
    _online_backup,
    _open_market,
    _sha256,
    _validate_against_market,
)


CONFIRMATION = "project-supervised-coin-group-review-batch"


def _review_values(review: object) -> tuple[object, ...]:
    return tuple(
        getattr(review, field)
        for field in (
            "event_type",
            "group_number",
            "source_event_time_utc",
            "event_confirmed",
            "commodity_code",
            "side",
            "price_project_thousand_toman",
            "quantity",
            "settlement_term",
            "trade_form",
            "is_conditional",
        )
    )


def _decision_values(decision: dict[str, object]) -> tuple[object, ...]:
    return (
        decision["event_type"],
        decision["group_number"],
        decision["source_event_time_utc"],
        decision["event_confirmed"],
        decision["commodity_code"],
        decision["side"],
        decision["price_project_thousand_toman"],
        decision["quantity"],
        decision["settlement_term"],
        decision["trade_form"],
        decision["is_conditional"],
    )


def run(args: argparse.Namespace) -> dict[str, object]:
    payload = _load_pack(args.batch, args.expected_sha256)
    decisions = payload["decisions"]
    market = _open_market(args.market_store)
    try:
        _validate_against_market(market, decisions)
    finally:
        market.close()
    feedback = load_coin_group_parser_feedback(args.feedback_store)
    reviews = []
    for decision in decisions:
        key = bytes.fromhex(str(decision["event_key"]))
        review = feedback.get(key)
        if review is None or _review_values(review) != _decision_values(decision):
            raise ReviewBatchError("review_projection_feedback_mismatch")
        reviews.append(review)
    if not args.apply:
        return {
            "status": "VALIDATED_FOR_PROJECTION",
            "decisions": len(reviews),
            "batch_sha256": args.expected_sha256.lower(),
        }
    if (
        args.confirm != CONFIRMATION
        or args.market_backup is None
        or args.feedback_backup is None
    ):
        raise ReviewBatchError("review_projection_confirmation_required")
    _online_backup(args.market_store, args.market_backup)
    _online_backup(args.feedback_store, args.feedback_backup)
    writable = connect_market_store(args.market_store)
    try:
        writable.execute("BEGIN IMMEDIATE")
        report = project_coin_group_reviews(writable, reviews)
        trade_report = reconcile_pending_trades_from_reviewed_roots(
            writable,
            cutoff_utc=min(item.source_event_time_utc for item in reviews),
        )
        writable.commit()
        check = writable.execute("PRAGMA quick_check").fetchone()
        if check is None or str(check[0]).lower() != "ok":
            raise ReviewBatchError("review_projection_market_check_failed")
    except BaseException:
        writable.rollback()
        raise
    finally:
        writable.close()
    applied = mark_coin_group_parser_feedback_applied(
        args.feedback_store,
        report.event_keys,
    )
    remaining = load_coin_group_parser_feedback(args.feedback_store)
    if any(
        remaining[key].applied_revision < remaining[key].review_revision
        for key in report.event_keys
    ):
        raise ReviewBatchError("review_projection_feedback_not_marked")
    return {
        "status": "PROJECTED",
        "submitted": report.submitted,
        "projected": report.projected,
        "eligible": report.eligible,
        "rejected": report.rejected,
        "unchanged": report.unchanged,
        "feedback_marked_applied": applied,
        "reviewed_root_trades_projected": trade_report.projected,
        "reviewed_root_trades_eligible": trade_report.eligible,
        "reviewed_root_trades_rejected": trade_report.rejected,
        "batch_sha256": args.expected_sha256.lower(),
        "market_backup_sha256": _sha256(args.market_backup),
        "feedback_backup_sha256": _sha256(args.feedback_backup),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--market-store", type=Path, required=True)
    parser.add_argument("--feedback-store", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--market-backup", type=Path)
    parser.add_argument("--feedback-backup", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = run(build_parser().parse_args(argv))
    except (
        ReviewBatchError,
        CoinGroupFeedbackError,
        CoinGroupReviewProjectionError,
        OSError,
        sqlite3.Error,
        ValueError,
    ):
        print(
            json.dumps(
                {"status": "FAILED", "reason": "REVIEW_PROJECTION_FAILED"},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
