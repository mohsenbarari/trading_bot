from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from core.market_intelligence.coin_group_feedback import (
    CoinGroupFeedbackError,
    load_coin_group_parser_feedback,
    mark_coin_group_parser_feedback_applied,
    record_coin_group_parser_feedback,
    record_coin_group_parser_feedback_batch,
)
from core.market_intelligence.coin_group_pipeline import process_coin_group_staging
from core.market_intelligence.coin_group_review_projection import (
    CoinGroupReviewProjectionError,
    project_coin_group_reviews,
    reconcile_pending_trades_from_reviewed_roots,
)
from core.market_intelligence.coin_group_staging import (
    CoinGroupStagingMessage,
    connect_coin_group_staging,
    initialize_coin_group_staging,
    stage_coin_group_message,
)
from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from core.market_intelligence.shadow_legacy_bridge import parser_version_allowed


def _record_feedback(path: Path, event_key: bytes, *, reviewed_at: str):
    return record_coin_group_parser_feedback(
        path,
        event_key=event_key,
        event_type="OFFER",
        group_number=1,
        source_event_time_utc="2026-08-15T10:00:00Z",
        ambiguous_fields=["commodity"],
        event_confirmed=True,
        commodity_code="IMAM",
        side="SELL",
        price_project_thousand_toman=188_600,
        quantity=5,
        settlement_term="TOMORROW",
        trade_form="PHYSICAL",
        is_conditional=False,
        reviewer="operator",
        reviewed_at_utc=reviewed_at,
    )


def test_feedback_sidecar_is_structured_revisioned_and_privacy_safe() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "feedback.sqlite3"
        key = derive_event_key("coin-group-offer-v1", 1, 101, 0)
        first = _record_feedback(path, key, reviewed_at="2026-08-15T10:01:00Z")
        second = _record_feedback(path, key, reviewed_at="2026-08-15T10:02:00Z")
        assert (first.review_revision, second.review_revision) == (1, 2)
        loaded = load_coin_group_parser_feedback(path)[key]
        assert loaded.ambiguous_fields == frozenset({"commodity"})
        assert loaded.applied_at_utc is None
        assert mark_coin_group_parser_feedback_applied(
            path, [key], applied_at_utc="2026-08-15T10:02:15Z"
        ) == 1
        assert mark_coin_group_parser_feedback_applied(
            path, [key], applied_at_utc="2026-08-15T10:02:20Z"
        ) == 0

        connection = sqlite3.connect(path)
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(coin_group_parser_feedback)"
            )
        }
        row = connection.execute(
            "SELECT reviewer_digest,ambiguous_fields_json,application_count "
            "FROM coin_group_parser_feedback"
        ).fetchone()
        connection.close()
        assert not columns.intersection(
            {"raw_text", "message_text", "message_id", "sender_name", "reviewer"}
        )
        assert len(bytes(row[0])) == 32
        assert json.loads(row[1]) == ["commodity"]
        assert row[2] == 1


def _batch_decision(event_key: bytes, *, price: int = 188_600) -> dict[str, object]:
    return {
        "event_key": event_key,
        "event_type": "OFFER",
        "group_number": 1,
        "source_event_time_utc": "2026-08-15T10:00:00Z",
        "ambiguous_fields": ["commodity"],
        "event_confirmed": True,
        "commodity_code": "IMAM",
        "side": "SELL",
        "price_project_thousand_toman": price,
        "quantity": 5,
        "settlement_term": "TOMORROW",
        "trade_form": "PHYSICAL",
        "is_conditional": False,
    }


def test_feedback_batch_is_atomic_revisioned_and_idempotent() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "feedback.sqlite3"
        first = derive_event_key("batch", 1)
        second = derive_event_key("batch", 2)
        decisions = [_batch_decision(first), _batch_decision(second)]
        result = record_coin_group_parser_feedback_batch(
            path,
            decisions,
            reviewer="supervised-audit",
            reviewed_at_utc="2026-08-15T10:05:00Z",
        )
        assert result == {"submitted": 2, "recorded": 2, "unchanged": 0}
        replay = record_coin_group_parser_feedback_batch(
            path,
            decisions,
            reviewer="supervised-audit",
            reviewed_at_utc="2026-08-15T10:06:00Z",
        )
        assert replay == {"submitted": 2, "recorded": 0, "unchanged": 2}
        changed = record_coin_group_parser_feedback_batch(
            path,
            [_batch_decision(first, price=188_700)],
            reviewer="supervised-audit",
            reviewed_at_utc="2026-08-15T10:07:00Z",
        )
        assert changed == {"submitted": 1, "recorded": 1, "unchanged": 0}
        loaded = load_coin_group_parser_feedback(path)
        assert loaded[first].review_revision == 2
        assert loaded[second].review_revision == 1


def test_feedback_batch_rejects_all_rows_before_writing() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "feedback.sqlite3"
        valid = _batch_decision(derive_event_key("batch-valid", 1))
        invalid = _batch_decision(derive_event_key("batch-invalid", 2))
        invalid["event_confirmed"] = "yes"
        try:
            record_coin_group_parser_feedback_batch(
                path,
                [valid, invalid],
                reviewer="supervised-audit",
                reviewed_at_utc="2026-08-15T10:05:00Z",
            )
        except CoinGroupFeedbackError:
            pass
        else:
            raise AssertionError("invalid batch unexpectedly accepted")
        assert load_coin_group_parser_feedback(path) == {}


def _pending_observation(event_key: bytes) -> MarketObservation:
    return MarketObservation(
        event_key=event_key,
        source_code="GROUP_1",
        source_family="GROUP",
        event_time_utc="2026-08-15T10:00:00Z",
        available_at_utc="2026-08-15T10:00:01Z",
        instrument="COIN_UNRESOLVED",
        market_label="GROUP_COIN_UNRESOLVED",
        settlement_term="TOMORROW",
        trade_form="PHYSICAL",
        event_type="OFFER",
        side="SELL",
        price=188_600,
        price_unit="PROJECT_THOUSAND_TOMAN",
        currency="TOMAN",
        quantity=5,
        quantity_unit="COIN",
        parse_confidence=0.25,
        parser_version="coin-parser-test",
        quality_state="PENDING_REVIEW",
        quality_policy_version="coin-policy-test",
        is_conditional=False,
        attributes={"resolution_reason": "COMMODITY_AMBIGUOUS"},
    )


def test_exact_event_review_projection_is_causal_idempotent_and_rejectable() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        market = connect_market_store(root / "market.sqlite3")
        initialize_market_store(market)
        accepted_key = derive_event_key("review-projection", 1)
        rejected_key = derive_event_key("review-projection", 2)
        upsert_observation(market, _pending_observation(accepted_key))
        upsert_observation(market, _pending_observation(rejected_key))
        market.commit()
        feedback = root / "feedback.sqlite3"
        accepted = _record_feedback(
            feedback,
            accepted_key,
            reviewed_at="2026-08-15T12:00:00Z",
        )
        rejected = record_coin_group_parser_feedback(
            feedback,
            event_key=rejected_key,
            event_type="OFFER",
            group_number=1,
            source_event_time_utc="2026-08-15T10:00:00Z",
            ambiguous_fields=["commodity", "event_validity"],
            event_confirmed=False,
            commodity_code="UNRESOLVED",
            side="SELL",
            price_project_thousand_toman=188_600,
            quantity=5,
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
            is_conditional=False,
            reviewer="operator",
            reviewed_at_utc="2026-08-15T12:00:00Z",
        )
        report = project_coin_group_reviews(market, [accepted, rejected])
        market.commit()
        assert (report.projected, report.eligible, report.rejected) == (2, 1, 1)
        accepted_row = market.execute(
            "SELECT * FROM market_observations WHERE event_key=?", (accepted_key,)
        ).fetchone()
        rejected_row = market.execute(
            "SELECT * FROM market_observations WHERE event_key=?", (rejected_key,)
        ).fetchone()
        assert accepted_row["instrument"] == "COIN_IMAM"
        assert accepted_row["quality_state"] == "ELIGIBLE"
        assert accepted_row["available_at_utc"] == "2026-08-15T12:00:00Z"
        assert len(accepted_row["parser_version"]) <= 96
        assert parser_version_allowed(accepted_row["parser_version"])
        accepted_attributes = json.loads(accepted_row["attributes_json"])
        assert accepted_attributes["supervised_review_revision"] == 1
        assert "human_feedback_syntax_fingerprint" not in accepted_attributes
        assert rejected_row["instrument"] == "COIN_UNRESOLVED"
        assert rejected_row["quality_state"] == "REJECTED"
        replay = project_coin_group_reviews(market, [accepted, rejected])
        market.commit()
        assert (replay.projected, replay.unchanged) == (0, 2)
        market.close()


def test_exact_event_review_projection_validates_batch_before_writing() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        market = connect_market_store(root / "market.sqlite3")
        initialize_market_store(market)
        valid_key = derive_event_key("review-projection-valid", 1)
        missing_key = derive_event_key("review-projection-missing", 2)
        upsert_observation(market, _pending_observation(valid_key))
        market.commit()
        feedback = root / "feedback.sqlite3"
        valid = _record_feedback(
            feedback, valid_key, reviewed_at="2026-08-15T12:00:00Z"
        )
        missing = _record_feedback(
            feedback, missing_key, reviewed_at="2026-08-15T12:00:00Z"
        )
        try:
            project_coin_group_reviews(market, [valid, missing])
        except CoinGroupReviewProjectionError as exc:
            assert str(exc) == "review_projection_event_missing"
        else:
            raise AssertionError("incomplete exact-event batch unexpectedly projected")
        row = market.execute(
            "SELECT quality_state FROM market_observations WHERE event_key=?",
            (valid_key,),
        ).fetchone()
        assert row["quality_state"] == "PENDING_REVIEW"
        market.close()


def _pending_trade(
    event_key: bytes,
    root_key: bytes,
    *,
    reason: str,
) -> MarketObservation:
    return MarketObservation(
        event_key=event_key,
        source_code="GROUP_1",
        source_family="GROUP",
        event_time_utc="2026-08-15T10:05:00Z",
        available_at_utc="2026-08-15T10:05:01Z",
        instrument="COIN_UNRESOLVED",
        market_label="GROUP_COIN_UNRESOLVED",
        settlement_term="TOMORROW",
        trade_form="PHYSICAL",
        event_type="TRADE",
        side="SELL",
        price=188_600,
        price_unit="PROJECT_THOUSAND_TOMAN",
        currency="TOMAN",
        quantity=5,
        quantity_unit="COIN",
        parse_confidence=0.2,
        parser_version="coin-trade-test",
        quality_state="PENDING_REVIEW",
        quality_policy_version="coin-trade-policy-test",
        is_conditional=False,
        attributes={
            "root_offer_event_key": root_key.hex(),
            "resolution_reason": reason,
        },
    )


def test_reviewed_root_trade_reconciliation_accepts_only_root_only_blocker() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        market = connect_market_store(root / "market.sqlite3")
        initialize_market_store(market)
        eligible_root = derive_event_key("trade-review-root", 1)
        invalid_trade_root = derive_event_key("trade-review-root", 2)
        rejected_root = derive_event_key("trade-review-root", 3)
        unreviewed_root = derive_event_key("trade-review-root", 4)
        for key in (eligible_root, invalid_trade_root, rejected_root, unreviewed_root):
            upsert_observation(market, _pending_observation(key))
        eligible_trade = derive_event_key("trade-review", 1)
        invalid_trade = derive_event_key("trade-review", 2)
        rejected_root_trade = derive_event_key("trade-review", 3)
        hard_blocked_trade = derive_event_key("trade-review", 4)
        upsert_observation(
            market,
            _pending_trade(
                eligible_trade,
                eligible_root,
                reason="ROOT_OFFER_NOT_MODEL_ELIGIBLE:ANCHORS",
            ),
        )
        upsert_observation(
            market,
            _pending_trade(
                invalid_trade,
                invalid_trade_root,
                reason=(
                    "ROOT_OFFER_NOT_MODEL_ELIGIBLE:ANCHORS;"
                    "NON_AGGREGATE_FILL_EXCEEDS_REMAINING_ROOT_QUANTITY"
                ),
            ),
        )
        upsert_observation(
            market,
            _pending_trade(
                rejected_root_trade,
                rejected_root,
                reason="ROOT_OFFER_NOT_MODEL_ELIGIBLE:ANCHORS",
            ),
        )
        upsert_observation(
            market,
            _pending_trade(
                hard_blocked_trade,
                unreviewed_root,
                reason="COUNTERPARTY_DECLARATION_REQUIRES_OFFERER_CONFIRMATION",
            ),
        )
        market.commit()
        feedback = root / "feedback.sqlite3"
        accepted_one = _record_feedback(
            feedback, eligible_root, reviewed_at="2026-08-15T12:00:00Z"
        )
        accepted_two = _record_feedback(
            feedback, invalid_trade_root, reviewed_at="2026-08-15T12:00:00Z"
        )
        rejected = record_coin_group_parser_feedback(
            feedback,
            event_key=rejected_root,
            event_type="OFFER",
            group_number=1,
            source_event_time_utc="2026-08-15T10:00:00Z",
            ambiguous_fields=["commodity", "event_validity"],
            event_confirmed=False,
            commodity_code="UNRESOLVED",
            side="SELL",
            price_project_thousand_toman=188_600,
            quantity=5,
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
            is_conditional=False,
            reviewer="operator",
            reviewed_at_utc="2026-08-15T12:00:00Z",
        )
        project_coin_group_reviews(market, [accepted_one, accepted_two, rejected])
        report = reconcile_pending_trades_from_reviewed_roots(
            market, cutoff_utc="2026-08-15T00:00:00Z"
        )
        market.commit()
        assert (report.considered, report.eligible, report.rejected) == (4, 1, 3)
        replay = reconcile_pending_trades_from_reviewed_roots(
            market, cutoff_utc="2026-08-15T00:00:00Z"
        )
        market.commit()
        assert (replay.projected, replay.unchanged) == (0, 4)
        states = {
            bytes(row["event_key"]): str(row["quality_state"])
            for row in market.execute(
                "SELECT event_key,quality_state FROM market_observations "
                "WHERE event_type='TRADE'"
            )
        }
        assert states == {
            eligible_trade: "ELIGIBLE",
            invalid_trade: "REJECTED",
            rejected_root_trade: "REJECTED",
            hard_blocked_trade: "REJECTED",
        }
        eligible = market.execute(
            "SELECT instrument,available_at_utc,parser_version,attributes_json "
            "FROM market_observations WHERE event_key=?",
            (eligible_trade,),
        ).fetchone()
        assert eligible["instrument"] == "COIN_IMAM"
        assert eligible["available_at_utc"] == "2026-08-15T12:00:00Z"
        assert len(eligible["parser_version"]) <= 96
        assert parser_version_allowed(eligible["parser_version"])
        attributes = json.loads(eligible["attributes_json"])
        assert attributes["resolution_reason"] == "SUPERVISED_REVIEWED_ROOT_TRADE"
        market.close()


def test_feedback_corrects_exact_event_and_calibrates_later_similar_offer() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        staging = connect_coin_group_staging(root / "staging.sqlite3")
        initialize_coin_group_staging(staging)
        market = connect_market_store(root / "market.sqlite3")
        initialize_market_store(market)
        first_key = derive_event_key("coin-group-offer-v1", 1, 101, 0)
        feedback_path = root / "feedback.sqlite3"
        _record_feedback(
            feedback_path,
            first_key,
            reviewed_at="2026-08-15T10:01:00Z",
        )
        for message_id, event_time, available_at, text in (
            (101, "2026-08-15T10:00:00Z", "2026-08-15T10:00:02Z", "5 تا 188/600 ف"),
            (102, "2026-08-15T10:02:00Z", "2026-08-15T10:02:02Z", "3 تا 188/700 خ"),
        ):
            stage_coin_group_message(
                staging,
                CoinGroupStagingMessage(
                    group_number=1,
                    message_id=message_id,
                    event_time_utc=event_time,
                    available_at_utc=available_at,
                    text=text,
                    sender_identity=f"sender-{message_id}",
                ),
            )
        staging.commit()

        report = process_coin_group_staging(
            staging,
            market,
            as_of_utc="2026-08-15T10:03:00Z",
            parser_feedback=load_coin_group_parser_feedback(feedback_path),
        )
        market.commit()
        rows = market.execute(
            """
            SELECT event_key,instrument,side,price_num,quantity_num,
                   quality_state,available_at_utc,parser_version,attributes_json
            FROM market_observations WHERE event_type='OFFER'
            ORDER BY event_time_utc
            """
        ).fetchall()
        assert (report.feedback_reviews_seen, report.feedback_reviews_applied) == (1, 1)
        assert report.applied_feedback_event_keys == (first_key,)
        assert len(rows) == 2
        assert (
            rows[0]["instrument"],
            rows[0]["side"],
            int(rows[0]["price_num"]),
            int(rows[0]["quantity_num"]),
            rows[0]["quality_state"],
        ) == ("COIN_IMAM", "SELL", 188_600, 5, "ELIGIBLE")
        assert rows[0]["available_at_utc"] == "2026-08-15T10:03:00Z"
        assert "human-feedback-r1" in rows[0]["parser_version"]
        reviewed_attributes = json.loads(rows[0]["attributes_json"])
        assert reviewed_attributes["human_feedback_revision"] == 1
        assert reviewed_attributes["field_evidence"]["instrument"] == [
            "HUMAN_REVIEWED_CORRECTION"
        ]
        assert reviewed_attributes["field_evidence"]["price"] == [
            "MESSAGE_NUMERIC_GRAMMAR"
        ]
        assert (rows[1]["instrument"], rows[1]["quality_state"]) == (
            "COIN_IMAM",
            "ELIGIBLE",
        )
        staging.close()
        market.close()


def test_feedback_anchor_never_leaks_before_review_availability() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        staging = connect_coin_group_staging(root / "staging.sqlite3")
        initialize_coin_group_staging(staging)
        market = connect_market_store(root / "market.sqlite3")
        initialize_market_store(market)
        first_key = derive_event_key("coin-group-offer-v1", 1, 101, 0)
        feedback_path = root / "feedback.sqlite3"
        _record_feedback(
            feedback_path,
            first_key,
            reviewed_at="2026-08-15T10:01:00Z",
        )
        stage_coin_group_message(
            staging,
            CoinGroupStagingMessage(
                group_number=1,
                message_id=102,
                event_time_utc="2026-08-15T10:00:30Z",
                available_at_utc="2026-08-15T10:00:32Z",
                text="3 تا 188/700 خ",
            ),
        )
        staging.commit()
        process_coin_group_staging(
            staging,
            market,
            as_of_utc="2026-08-15T10:03:00Z",
            parser_feedback=load_coin_group_parser_feedback(feedback_path),
        )
        row = market.execute(
            "SELECT instrument,quality_state FROM market_observations WHERE event_type='OFFER'"
        ).fetchone()
        assert (row["instrument"], row["quality_state"]) == (
            "COIN_UNRESOLVED",
            "PENDING_REVIEW",
        )
        staging.close()
        market.close()


def test_feedback_can_reject_false_event_without_becoming_price_anchor() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        staging = connect_coin_group_staging(root / "staging.sqlite3")
        initialize_coin_group_staging(staging)
        market = connect_market_store(root / "market.sqlite3")
        initialize_market_store(market)
        first_key = derive_event_key("coin-group-offer-v1", 1, 101, 0)
        feedback_path = root / "feedback.sqlite3"
        record_coin_group_parser_feedback(
            feedback_path,
            event_key=first_key,
            event_type="OFFER",
            group_number=1,
            source_event_time_utc="2026-08-15T10:00:00Z",
            ambiguous_fields=["event_validity"],
            event_confirmed=False,
            commodity_code="UNRESOLVED",
            side="SELL",
            price_project_thousand_toman=188_600,
            quantity=5,
            settlement_term="TOMORROW",
            trade_form="PHYSICAL",
            is_conditional=False,
            reviewer="operator",
            reviewed_at_utc="2026-08-15T10:01:00Z",
        )
        for message_id, event_time, text in (
            (101, "2026-08-15T10:00:00Z", "5 تا 188/600 ف"),
            (102, "2026-08-15T10:02:00Z", "3 تا 188/700 خ"),
        ):
            stage_coin_group_message(
                staging,
                CoinGroupStagingMessage(
                    group_number=1,
                    message_id=message_id,
                    event_time_utc=event_time,
                    available_at_utc=event_time,
                    text=text,
                    sender_identity=f"sender-{message_id}",
                ),
            )
        staging.commit()

        report = process_coin_group_staging(
            staging,
            market,
            as_of_utc="2026-08-15T10:03:00Z",
            parser_feedback=load_coin_group_parser_feedback(feedback_path),
        )
        rows = market.execute(
            "SELECT event_key,instrument,quality_state,parse_confidence "
            "FROM market_observations "
            "WHERE event_type='OFFER' ORDER BY event_time_utc"
        ).fetchall()
        assert report.feedback_reviews_applied == 1
        assert (rows[0]["event_key"], rows[0]["quality_state"]) == (
            first_key,
            "REJECTED",
        )
        assert rows[0]["parse_confidence"] == 0.0
        assert (rows[1]["instrument"], rows[1]["quality_state"]) == (
            "COIN_UNRESOLVED",
            "PENDING_REVIEW",
        )
        staging.close()
        market.close()


def test_feedback_corrects_negotiated_trade_fact_after_reply_linking() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        staging = connect_coin_group_staging(root / "staging.sqlite3")
        initialize_coin_group_staging(staging)
        market = connect_market_store(root / "market.sqlite3")
        initialize_market_store(market)
        feedback_path = root / "feedback.sqlite3"
        offer_key = derive_event_key("coin-group-offer-v1", 1, 101, 0)
        trade_key = derive_event_key("coin-group-trade-v1", 1, 101, 103)
        _record_feedback(
            feedback_path,
            offer_key,
            reviewed_at="2026-08-15T10:01:00Z",
        )
        record_coin_group_parser_feedback(
            feedback_path,
            event_key=trade_key,
            event_type="TRADE",
            group_number=1,
            source_event_time_utc="2026-08-15T10:00:04Z",
            ambiguous_fields=["price", "quantity", "settlement"],
            event_confirmed=True,
            commodity_code="IMAM",
            side="SELL",
            price_project_thousand_toman=188_500,
            quantity=2,
            settlement_term="CASH",
            trade_form="PHYSICAL",
            is_conditional=False,
            reviewer="operator",
            reviewed_at_utc="2026-08-15T10:01:01Z",
        )
        for item in (
            CoinGroupStagingMessage(
                group_number=1,
                message_id=101,
                event_time_utc="2026-08-15T10:00:00Z",
                available_at_utc="2026-08-15T10:00:01Z",
                text="5 تا 188/600 ف",
                sender_identity="owner",
            ),
            CoinGroupStagingMessage(
                group_number=1,
                message_id=102,
                event_time_utc="2026-08-15T10:00:02Z",
                available_at_utc="2026-08-15T10:00:03Z",
                text="3 تا خریدم",
                reply_to_message_id=101,
                sender_identity="buyer",
            ),
            CoinGroupStagingMessage(
                group_number=1,
                message_id=103,
                event_time_utc="2026-08-15T10:00:04Z",
                available_at_utc="2026-08-15T10:00:05Z",
                text="برکت",
                reply_to_message_id=102,
                sender_identity="owner",
            ),
        ):
            stage_coin_group_message(staging, item)
        staging.commit()

        report = process_coin_group_staging(
            staging,
            market,
            as_of_utc="2026-08-15T10:02:00Z",
            parser_feedback=load_coin_group_parser_feedback(feedback_path),
        )
        trade = market.execute(
            "SELECT event_key,instrument,side,price_num,quantity_num,"
            "settlement_term,quality_state,parser_version "
            "FROM market_observations WHERE event_type='TRADE'"
        ).fetchone()
        assert report.feedback_reviews_applied == 2
        assert set(report.applied_feedback_event_keys) == {offer_key, trade_key}
        assert (
            trade["event_key"],
            trade["instrument"],
            trade["side"],
            int(trade["price_num"]),
            int(trade["quantity_num"]),
            trade["settlement_term"],
            trade["quality_state"],
        ) == (trade_key, "COIN_IMAM", "SELL", 188_500, 2, "CASH", "ELIGIBLE")
        assert "human-feedback-r1" in trade["parser_version"]
        staging.close()
        market.close()


def test_feedback_generalizes_only_linguistic_fields_not_economic_identity() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        staging = connect_coin_group_staging(root / "staging.sqlite3")
        initialize_coin_group_staging(staging)
        market = connect_market_store(root / "market.sqlite3")
        initialize_market_store(market)
        feedback_path = root / "feedback.sqlite3"
        first_key = derive_event_key("coin-group-offer-v1", 1, 101, 0)
        record_coin_group_parser_feedback(
            feedback_path,
            event_key=first_key,
            event_type="OFFER",
            group_number=1,
            source_event_time_utc="2026-08-15T10:00:00Z",
            ambiguous_fields=["commodity", "settlement"],
            event_confirmed=True,
            commodity_code="IMAM",
            side="SELL",
            price_project_thousand_toman=188_600,
            quantity=5,
            settlement_term="CASH",
            trade_form="PHYSICAL",
            is_conditional=False,
            reviewer="operator",
            reviewed_at_utc="2026-08-15T10:01:00Z",
        )
        stage_coin_group_message(
            staging,
            CoinGroupStagingMessage(
                group_number=1,
                message_id=101,
                event_time_utc="2026-08-15T10:00:00Z",
                available_at_utc="2026-08-15T10:00:02Z",
                text="5 تا 188/600 ف",
                sender_identity="first",
            ),
        )
        stage_coin_group_message(
            staging,
            CoinGroupStagingMessage(
                group_number=1,
                message_id=102,
                event_time_utc="2026-08-15T10:00:30Z",
                available_at_utc="2026-08-15T10:00:32Z",
                text="3 تا 188/700 ف",
                sender_identity="before-review",
            ),
        )
        staging.commit()
        process_coin_group_staging(
            staging,
            market,
            as_of_utc="2026-08-15T10:01:30Z",
            parser_feedback=load_coin_group_parser_feedback(feedback_path),
        )
        market.commit()

        stage_coin_group_message(
            staging,
            CoinGroupStagingMessage(
                group_number=1,
                message_id=103,
                event_time_utc="2026-08-15T10:02:00Z",
                available_at_utc="2026-08-15T10:02:02Z",
                text="4 تا 188/800 ف",
                sender_identity="second",
            ),
        )
        staging.commit()
        report = process_coin_group_staging(
            staging,
            market,
            as_of_utc="2026-08-15T10:03:00Z",
            parser_feedback=load_coin_group_parser_feedback(feedback_path),
        )
        second = market.execute(
            "SELECT instrument,settlement_term,quality_state,parser_version,"
            "attributes_json FROM market_observations WHERE event_key=?",
            (derive_event_key("coin-group-offer-v1", 1, 103, 0),),
        ).fetchone()
        before_review = market.execute(
            "SELECT instrument,settlement_term,quality_state "
            "FROM market_observations WHERE event_key=?",
            (derive_event_key("coin-group-offer-v1", 1, 102, 0),),
        ).fetchone()
        attributes = json.loads(second["attributes_json"])
        assert report.feedback_pattern_calibrations_applied == 0
        assert (
            second["instrument"],
            second["settlement_term"],
            second["quality_state"],
        ) == ("COIN_UNRESOLVED", "TOMORROW", "PENDING_REVIEW")
        assert "human-pattern-r1" not in second["parser_version"]
        assert tuple(before_review) == (
            "COIN_UNRESOLVED",
            "TOMORROW",
            "PENDING_REVIEW",
        )
        assert "human_pattern_calibration_fields" not in attributes
        assert "human_feedback_syntax_fingerprint" not in attributes
        staging.close()
        market.close()


def test_trade_pattern_calibration_is_bound_to_reply_chain_and_root_commodity() -> None:
    """A generic final acknowledgement must not relabel another coin family."""

    with TemporaryDirectory() as directory:
        root = Path(directory)
        staging = connect_coin_group_staging(root / "staging.sqlite3")
        initialize_coin_group_staging(staging)
        market = connect_market_store(root / "market.sqlite3")
        initialize_market_store(market)
        feedback_path = root / "feedback.sqlite3"
        reviewed_trade_key = derive_event_key(
            "coin-group-trade-v1", 1, 101, 103
        )
        record_coin_group_parser_feedback(
            feedback_path,
            event_key=reviewed_trade_key,
            event_type="TRADE",
            group_number=1,
            source_event_time_utc="2026-08-17T09:00:04Z",
            ambiguous_fields=["commodity"],
            event_confirmed=True,
            commodity_code="IMAM",
            side="SELL",
            price_project_thousand_toman=188_600,
            quantity=5,
            settlement_term="CASH",
            trade_form="PHYSICAL",
            is_conditional=False,
            reviewer="operator",
            reviewed_at_utc="2026-08-17T09:01:00Z",
        )
        for item in (
            CoinGroupStagingMessage(
                group_number=1,
                message_id=101,
                event_time_utc="2026-08-17T09:00:00Z",
                available_at_utc="2026-08-17T09:00:01Z",
                text="۵ امام ف نقدی ۱۸۸۶۰۰",
                sender_identity="first-owner",
            ),
            CoinGroupStagingMessage(
                group_number=1,
                message_id=102,
                event_time_utc="2026-08-17T09:00:02Z",
                available_at_utc="2026-08-17T09:00:03Z",
                text="۵ خریدم",
                reply_to_message_id=101,
                sender_identity="first-buyer",
            ),
            CoinGroupStagingMessage(
                group_number=1,
                message_id=103,
                event_time_utc="2026-08-17T09:00:04Z",
                available_at_utc="2026-08-17T09:00:05Z",
                text="برکت",
                reply_to_message_id=102,
                sender_identity="first-owner",
            ),
            CoinGroupStagingMessage(
                group_number=1,
                message_id=201,
                event_time_utc="2026-08-17T09:02:00Z",
                available_at_utc="2026-08-17T09:02:01Z",
                text="۴ نیم خ نقدی ۹۴۵۰۰",
                sender_identity="second-owner",
            ),
            CoinGroupStagingMessage(
                group_number=1,
                message_id=202,
                event_time_utc="2026-08-17T09:02:02Z",
                available_at_utc="2026-08-17T09:02:03Z",
                text="۴ فروختم",
                reply_to_message_id=201,
                sender_identity="second-seller",
            ),
            CoinGroupStagingMessage(
                group_number=1,
                message_id=203,
                event_time_utc="2026-08-17T09:02:04Z",
                available_at_utc="2026-08-17T09:02:05Z",
                text="برکت",
                reply_to_message_id=202,
                sender_identity="second-owner",
            ),
        ):
            stage_coin_group_message(staging, item)
        staging.commit()

        process_coin_group_staging(
            staging,
            market,
            as_of_utc="2026-08-17T09:03:00Z",
            parser_feedback=load_coin_group_parser_feedback(feedback_path),
        )
        trades = market.execute(
            "SELECT event_key,instrument,price_num,quality_state,attributes_json "
            "FROM market_observations WHERE event_type='TRADE' "
            "ORDER BY event_time_utc"
        ).fetchall()

        assert len(trades) == 2
        assert trades[0]["event_key"] == reviewed_trade_key
        assert trades[0]["instrument"] == "COIN_IMAM"
        assert (
            trades[1]["instrument"],
            int(trades[1]["price_num"]),
            trades[1]["quality_state"],
        ) == ("COIN_HALF_BAHAR", 94_500, "ELIGIBLE")
        assert "human_pattern_calibration_revision" not in json.loads(
            trades[1]["attributes_json"]
        )
        staging.close()
        market.close()
