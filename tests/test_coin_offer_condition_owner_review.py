from __future__ import annotations

from scripts.build_coin_offer_condition_owner_review import select_owner_review_rows
from scripts.train_coin_offer_condition_classifier import TrainingRow, chronological_three_way_split


def _row(index: int) -> TrainingRow:
    conditional = index % 3 == 0
    return TrainingRow(
        opaque_digest=f"{index:040x}",
        group_code=f"group_{1 + index % 2}",
        source_partition="TEST",
        event_time_utc=f"2026-08-{1 + index // 20:02d}T{index % 20:02d}:00:00Z",
        settlement_term="CASH" if index % 2 else "TOMORROW",
        trade_form="PHYSICAL",
        model_text=f"متن <NUM> {index}",
        has_condition=conditional,
        families=("PAYMENT_ACCOUNT",) if conditional else (),
        session_phase="MID_SESSION" if index % 2 else "AFTER_HOURS",
        deadline_bucket="NO_DEADLINE",
        composite_class="test",
        span_tokens=("متن",),
        span_targets=(int(conditional),),
    )


def test_review_selection_is_deterministic_and_sealed() -> None:
    rows = [_row(index) for index in range(100)]
    _, _, evaluation = chronological_three_way_split(rows)

    first = select_owner_review_rows(rows, sample_count=12, seed=1729)
    second = select_owner_review_rows(rows, sample_count=12, seed=1729)

    assert [row.opaque_digest for row in first] == [row.opaque_digest for row in second]
    assert len(first) == 12
    assert {row.opaque_digest for row in first} <= {
        row.opaque_digest for row in evaluation
    }


def test_review_selection_rejects_impossible_size() -> None:
    rows = [_row(index) for index in range(20)]

    try:
        select_owner_review_rows(rows, sample_count=100)
    except ValueError as exc:
        assert str(exc) == "owner_review_sample_count_out_of_range"
    else:
        raise AssertionError("expected owner review size failure")
