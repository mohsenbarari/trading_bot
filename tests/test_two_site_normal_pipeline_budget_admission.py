"""New ordinary budget admission with legacy readers and quota routes unchanged."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scripts import two_site_proposal_byte_profile as profile
from scripts import two_site_proposal_pipeline as pipeline
from tests.test_two_site_cycle_tools import task
from tests.test_two_site_proposal_pipeline import fake_git


def declaration(value):
    allowed = value["allowed_files"]
    return profile.PREFIX + json.dumps(
        {"file_limits": {path: 24000 for path in allowed},
         "total_limit": 24000, "version": "1.0"},
        sort_keys=True, separators=(",", ":"))


def ready_task():
    value = task()
    value["requirements"].append(declaration(value))
    return value


def test_required_profile_pure_detached_and_legacy_reader_optional():
    value = ready_task()
    before = copy.deepcopy(value)
    parsed = profile.require_proposal_utf8_budget_profile(
        value["requirements"], value["allowed_files"])
    parsed["file_limits"][value["allowed_files"][0]] = 1
    assert value == before
    assert profile.require_proposal_utf8_budget_profile(
        value["requirements"], value["allowed_files"])["total_limit"] == 24000
    assert profile.parse_proposal_utf8_budget_profile(
        task()["requirements"], object(), object()) is None
    with pytest.raises(profile.ProposalByteProfileError, match=profile.ERROR):
        profile.require_proposal_utf8_budget_profile(
            task()["requirements"], task()["allowed_files"])
    assert value == before


def broken_task(good, fault):
    bad = copy.deepcopy(good)
    if fault == "missing":
        bad["requirements"].pop()
    elif fault == "duplicate":
        bad["requirements"].append(bad["requirements"][-1])
    elif fault == "wrong_paths":
        bad["requirements"][-1] = bad["requirements"][-1].replace(
            good["allowed_files"][0], "tests/test_other.py")
    else:
        bad["requirements"][-1] = profile.PREFIX + " " + bad["requirements"][-1][len(profile.PREFIX):]
    return bad


class ReservationReached(Exception):
    pass


@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong_paths", "noncanonical"])
def test_fresh_normal_pipeline_guards_before_reservation_provider_or_evidence(tmp_path, monkeypatch, fault):
    good = ready_task()
    monkeypatch.setenv("TWO_SITE_AUTOPILOT_RUN_ID", "cycle-0001-20990101T000000Z-123")
    # A stray maintenance variable must not suppress the ordinary guard.
    monkeypatch.setenv("TWO_SITE_MAINTENANCE_RUN_ID", "maint-20990101T000000Z-0123456789abcdef")
    monkeypatch.setattr(pipeline, "git", fake_git)
    reserve = Mock(side_effect=ReservationReached)
    provider = Mock(side_effect=AssertionError("provider must never run"))
    monkeypatch.setattr(pipeline, "reserve_semantic_attempt", reserve)
    monkeypatch.setattr(pipeline, "run_provider_session", provider)
    selected = {"task": good}
    monkeypatch.setattr(pipeline, "load_task", lambda *_args: (selected["task"], {}))
    def run(prefix):
        return pipeline.run_pipeline(tmp_path, tmp_path / "task.json", prefix, 30, None, {})
    def good_call(prefix):
        reserve.reset_mock()
        with pytest.raises(ReservationReached):
            run(prefix)
        assert reserve.call_count == 1
        provider.assert_not_called()
    good_call("before")
    bad = broken_task(good, fault)
    before = copy.deepcopy(bad)
    selected["task"] = bad
    reserve.reset_mock()
    base = tmp_path / ".local/two-site-refactor/pipeline"
    frozen = {p.name: p.read_bytes() for p in base.iterdir() if p.is_file()}
    with pytest.raises(pipeline.ProposalError, match="explicit valid UTF-8 budget"):
        run("bad")
    reserve.assert_not_called()
    provider.assert_not_called()
    assert not (base / "bad").exists()
    assert {p.name: p.read_bytes() for p in base.iterdir() if p.is_file()} == frozen
    assert bad == before
    selected["task"] = good
    good_call("after")
