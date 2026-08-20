from __future__ import annotations

from unittest.mock import patch

from scripts.train_coin_offer_condition_classifier import (
    TrainingRow,
    _evaluate_group_transfer,
    _fit_split,
    chronological_three_way_split,
)


def _row(index: int, *, group: str, condition: bool) -> TrainingRow:
    label = "شرط پرداخت" if condition else "لفظ عادی"
    return TrainingRow(
        opaque_digest=f"{index:040x}",
        group_code=group,
        source_partition="TEST",
        event_time_utc=f"2026-08-{index + 1:02d}T08:00:00Z",
        settlement_term="CASH",
        trade_form="PHYSICAL",
        model_text=label,
        has_condition=condition,
        families=("PAYMENT_DEADLINE",) if condition else (),
        session_phase="MID_SESSION",
        deadline_bucket="LE_60_MIN" if condition else "NO_DEADLINE",
        composite_class=f"class-{index}",
        span_tokens=tuple(label.split()),
        span_targets=(1, 1) if condition else (0, 0),
    )


def test_three_way_split_is_ordered_and_non_overlapping() -> None:
    rows = [_row(index, group="group_1", condition=index % 2 == 0) for index in range(20)]

    training, calibration, evaluation = chronological_three_way_split(rows)

    assert len(training) == 14
    assert len(calibration) == 3
    assert len(evaluation) == 3
    assert training + calibration + evaluation == rows
    assert {row.opaque_digest for row in training}.isdisjoint(
        {row.opaque_digest for row in calibration + evaluation}
    )
    assert {row.opaque_digest for row in calibration}.isdisjoint(
        {row.opaque_digest for row in evaluation}
    )


def test_abstention_policy_is_selected_on_calibration_not_evaluation() -> None:
    training = [_row(i, group="group_1", condition=i % 2 == 0) for i in range(40)]
    calibration = [
        _row(i + 40, group="group_1", condition=i < 10)
        for i in range(30)
    ]
    evaluation = [_row(i + 70, group="group_1", condition=i == 0) for i in range(5)]
    selected_support: list[tuple[int, int]] = []

    def select_policy(target, probability):
        selected_support.append((len(target), int(target.sum())))
        return {
            "status": "READY",
            "positive_threshold": 0.7,
            "negative_threshold": 0.3,
        }

    with patch(
        "scripts.train_coin_offer_condition_classifier.select_abstention_thresholds",
        side_effect=select_policy,
    ):
        _, _, _, _, metrics = _fit_split(
            training,
            calibration,
            evaluation,
            labels=("HAS_CONDITION",),
            min_label_support=2,
        )

    assert selected_support == [(len(calibration), 10)]
    assert metrics["HAS_CONDITION"]["support_positive"] == 1
    assert metrics["HAS_CONDITION"]["support_negative"] == 4
    assert metrics["HAS_CONDITION"]["calibration_positive"] == 10


def test_cross_group_transfer_never_calibrates_on_target_group() -> None:
    rows = [
        *[_row(i, group="group_1", condition=i % 2 == 0) for i in range(10)],
        *[_row(i + 10, group="group_2", condition=i % 2 == 0) for i in range(10)],
    ]

    with patch(
        "scripts.train_coin_offer_condition_classifier._fit_split",
        return_value=({}, {}, {}, {}, {}),
    ) as fit:
        result = _evaluate_group_transfer(
            rows,
            labels=("HAS_CONDITION",),
            min_label_support=2,
        )

    assert fit.call_count == 2
    for call in fit.call_args_list:
        training, calibration, evaluation = call.args[:3]
        assert {row.group_code for row in training} == {row.group_code for row in calibration}
        assert {row.group_code for row in training}.isdisjoint(
            {row.group_code for row in evaluation}
        )
    assert result["group_1_to_group_2"]["calibration_count"] == 2
    assert result["group_2_to_group_1"]["calibration_count"] == 2
