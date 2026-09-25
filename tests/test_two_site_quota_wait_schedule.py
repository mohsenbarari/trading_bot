"""Tests for pure two-site quota wait schedule calculation."""

import copy
import pytest
from scripts.two_site_quota_wait_schedule import (
    QuotaWaitScheduleError,
    initial_wait_schedule,
)

BASE_KNOWN = {
    "kind": "QUOTA_RESET_KNOWN",
    "provider": "gemini",
    "model": "gemini-2.5-pro",
    "conversation_id": "conv-123",
    "reset_at_utc": "2026-09-25T12:00:00Z",
}
BASE_UNKNOWN = {
    "kind": "QUOTA_RESET_UNKNOWN",
    "provider": "gemini",
    "model": "gemini-2.5-pro",
    "conversation_id": "conv-123",
    "reset_at_utc": None,
}


def test_positive_schedule_known_and_unknown_and_immutability():
    created = "2026-09-25T11:59:00Z"
    ev_known = copy.deepcopy(BASE_KNOWN)
    res_known = initial_wait_schedule(ev_known, created)

    expected_keys = {
        "reset_kind",
        "reset_at_utc",
        "created_at_utc",
        "next_check_at_utc",
        "remaining_check_budget",
        "remaining_resume_budget",
    }
    assert set(res_known.keys()) == expected_keys
    assert len(res_known) == 6
    assert ev_known == BASE_KNOWN
    assert res_known == {
        "reset_kind": "QUOTA_RESET_KNOWN",
        "reset_at_utc": "2026-09-25T12:00:00Z",
        "created_at_utc": "2026-09-25T11:59:00Z",
        "next_check_at_utc": "2026-09-25T12:00:30Z",
        "remaining_check_budget": 0,
        "remaining_resume_budget": 1,
    }
    assert type(res_known["remaining_check_budget"]) is int
    assert type(res_known["remaining_resume_budget"]) is int

    created_later = "2026-09-25T12:01:00Z"
    res_known_created_max = initial_wait_schedule(BASE_KNOWN, created_later)
    assert res_known_created_max["next_check_at_utc"] == "2026-09-25T12:01:30Z"

    ev_unknown = copy.deepcopy(BASE_UNKNOWN)
    res_unknown = initial_wait_schedule(ev_unknown, created)
    assert set(res_unknown.keys()) == expected_keys
    assert ev_unknown == BASE_UNKNOWN
    assert res_unknown == {
        "reset_kind": "QUOTA_RESET_UNKNOWN",
        "reset_at_utc": None,
        "created_at_utc": "2026-09-25T11:59:00Z",
        "next_check_at_utc": "2026-09-25T12:14:00Z",
        "remaining_check_budget": 5,
        "remaining_resume_budget": 1,
    }
    assert type(res_unknown["remaining_check_budget"]) is int


def test_positive_microseconds_and_year_0001():
    created_us = "2026-09-25T10:00:00.123456Z"
    reset_us = "2026-09-25T10:00:10.654321Z"
    ev = dict(BASE_KNOWN, reset_at_utc=reset_us)
    res = initial_wait_schedule(ev, created_us)
    assert res["next_check_at_utc"] == "2026-09-25T10:00:40.654321Z"

    created_y1 = "0001-01-01T00:00:00Z"
    res_y1 = initial_wait_schedule(BASE_UNKNOWN, created_y1)
    assert res_y1["next_check_at_utc"] == "0001-01-01T00:15:00Z"
    assert res_y1["next_check_at_utc"].startswith("0001-")


@pytest.mark.parametrize(
    "bad_kind",
    ["KNOWN", "UNKNOWN", ["QUOTA_RESET_KNOWN"], {"QUOTA_RESET_KNOWN": 1}],
)
def test_negative_kind_aliases_and_types(bad_kind):
    ev = dict(BASE_KNOWN, kind=bad_kind)
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(ev, "2026-09-25T10:00:00Z")


def test_negative_extra_int_key_and_dict_subclass():
    ev_extra = dict(BASE_KNOWN)
    ev_extra[123] = "unexpected"
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(ev_extra, "2026-09-25T10:00:00Z")

    class CustomDict(dict):
        pass

    ev_subclass = CustomDict(BASE_KNOWN)
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(ev_subclass, "2026-09-25T10:00:00Z")


@pytest.mark.parametrize(
    "field,bad_val",
    [
        ("provider", "openai"),
        ("provider", 123),
        ("model", ""),
        ("model", "m" * 129),
        ("model", "bad\nmodel"),
        ("conversation_id", ""),
        ("conversation_id", "has space"),
        ("conversation_id", "c" * 129),
        ("conversation_id", "bad\x00char"),
    ],
)
def test_negative_provider_model_conversation(field, bad_val):
    ev = dict(BASE_KNOWN, **{field: bad_val})
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(ev, "2026-09-25T10:00:00Z")


def test_negative_reset_at_mismatches():
    ev_unknown = dict(BASE_UNKNOWN, reset_at_utc="2026-09-25T12:00:00Z")
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(ev_unknown, "2026-09-25T10:00:00Z")

    ev_known = dict(BASE_KNOWN, reset_at_utc=None)
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(ev_known, "2026-09-25T10:00:00Z")


@pytest.mark.parametrize(
    "bad_ts",
    [
        "invalid-date",
        "2026-02-30T10:00:00Z",
        "2026-09-25T10:00:00+01:00",
        "2026-09-25T10:00:00",
    ],
)
def test_negative_malformed_and_offset_timestamps(bad_ts):
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(BASE_KNOWN, bad_ts)
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(
            dict(BASE_KNOWN, reset_at_utc=bad_ts), "2026-09-25T10:00:00Z"
        )


def test_negative_fractional_digits_both_created_and_reset():
    seven_frac = "2026-09-25T10:00:00.1234567Z"
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(BASE_KNOWN, seven_frac)
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(
            dict(BASE_KNOWN, reset_at_utc=seven_frac), "2026-09-25T10:00:00Z"
        )


def test_negative_year_9999_overflow_both_branches():
    ts_overflow_unknown = "9999-12-31T23:50:00Z"
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(BASE_UNKNOWN, ts_overflow_unknown)

    ts_overflow_known = "9999-12-31T23:59:45Z"
    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(
            dict(BASE_KNOWN, reset_at_utc="9999-12-31T23:00:00Z"),
            ts_overflow_known,
        )

    with pytest.raises(QuotaWaitScheduleError):
        initial_wait_schedule(
            dict(BASE_KNOWN, reset_at_utc=ts_overflow_known),
            "9999-12-31T23:00:00Z",
        )
