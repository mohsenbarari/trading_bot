"""Schema2 real-Git whole-output oracles; no accepted flags or collector mocks."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
from fractions import Fraction
import itertools
import json

import pytest

from tests.two_site_accepted_work_cost_support import (
    subject, ReportRepository, cost_record, groups, report_oracle, whole,
    rational, read_counter, PRIVATE, METRICS, canonical, digest,
)
from tests.test_two_site_accepted_work_cost_report_faults import add_revision

NS = 1_000_000_000
KINDS = ("active", "quota_wait", "planned_stop")
COUNTS = ("attempts", "worker_attempts", "reviewer_attempts", "failed_attempts", "successful_correction_rounds")
FIELDS = COUNTS + ("first_pass", "repeated_blocked")
REWORK = ("NONE", "REVIEWER_CHANGES_REQUIRED", "ATTEMPT_BLOCKED",
          "POST_REVIEW_CORRECTION", "MIXED", "UNCLASSIFIED_REWORK", "UNKNOWN")


def raw_pair(start=90, end=100, mono=5, duration=10):
    return {"start_utc_ns": int(Fraction(start) * NS),
            "start_monotonic_ns": int(Fraction(mono) * NS),
            "end_utc_ns": int(Fraction(end) * NS) if end is not None else None,
            "end_monotonic_ns": int((Fraction(mono) + Fraction(duration)) * NS) if end is not None else None}


def provider_declaration(first=True, counts=(2, 1, 1, 0, 0), path="NONE", initial=None, terminal="VERIFIED"):
    return {**dict(zip(COUNTS, counts)), "first_pass": first, "repeated_blocked": counts[3] >= 2,
            "rework_taxonomy": {"state": "OBSERVED",
                "initial_outcome": initial or ("FIRST_PASS" if first else "REVIEWER_CHANGES_REQUIRED"),
                "rework_path": path, "terminal_outcome": terminal}}


def blank():
    return {"state": "OBSERVATIONAL", "values": dict.fromkeys(KINDS),
            "states": dict.fromkeys(KINDS, "UNOBSERVED"),
            "provider": {**dict.fromkeys(FIELDS), "rework_taxonomy": None}}


def annotate(spec, *, start=90, end=100, mono=5, duration=10, first=True,
             counts=(2, 1, 1, 0, 0), path="NONE", initial=None, terminal="VERIFIED"):
    """Declare raw evidence AND independent expected observations, never via SUT."""
    run = spec["raw"]["run_id"]
    assert spec["raw"]["provider_metrics"]["attempts"] == counts[0]
    spec["raw"]["execution_observation"] = {
        "schema_version": 1, "clock_domain": run,
        "active": raw_pair(start, end, mono, duration), "quota_wait": None, "planned_stop": None}
    provider = provider_declaration(first, counts, path, initial, terminal)
    spec["raw"]["provider_metrics"].update({key: copy.deepcopy(value) for key, value in provider.items()
                                           if key != "repeated_blocked"})
    declaration = blank()
    declaration["values"]["active"] = Fraction(duration) if end is not None else None
    declaration["states"]["active"] = "OBSERVED" if end is not None else "OPEN_INTERVAL"
    declaration["provider"] = provider
    spec["measurement"] = declaration
    return spec


def declared_state(spec, kind, state, value=None):
    spec.setdefault("measurement", blank())["states"][kind] = state
    spec["measurement"]["values"][kind] = Fraction(value) if value is not None else None


def coverage(declarations):
    values = [Fraction(value) for state, value in declarations if state != "CONFLICT" and value is not None]
    conflicts = sum(state == "CONFLICT" for state, _ in declarations)
    unknown = sum(state != "CONFLICT" and value is None for state, value in declarations)
    total = sum(values, Fraction())
    return {"observed_group_count": len(values), "unknown_group_count": unknown,
            "conflict_group_count": conflicts, "observed_subtotal": rational(total),
            "full_total": rational(total) if values and not unknown and not conflicts else None}


def golden_measurements(repo, base, *, global_conflict=False, overlaps=False):
    """Whole expected wire from declared fixture facts, not parser/normalizer calls."""
    rows = []
    for group, cost in zip(groups(repo.specs), base["costs"]["rows"]):
        declaration = group["spec"].get("measurement", blank())
        rows.append({"metrics_ordinals": group["ordinals"], "window_state": cost["window_state"],
            "state": declaration["state"], "timing_states": copy.deepcopy(declaration["states"]),
            "timing_seconds": {key: rational(value) if value is not None else None
                               for key, value in declaration["values"].items()},
            "provider": copy.deepcopy(declaration["provider"])})
    selected = [row for row in rows if row["window_state"] == "SELECTED"]
    value = lambda number: Fraction(number["numerator"], number["denominator"]) if number is not None else None
    timing = {}
    for kind in KINDS:
        timing[kind + "_seconds"] = coverage([(row["state"], value(row["timing_seconds"][kind])) for row in selected])
    window = base["window"]
    calendar = Fraction(window["end_utc"] - window["start_utc"]) if window["kind"] == "UTC_HALF_OPEN" else None
    timing["calendar_seconds"] = rational(calendar) if calendar is not None else None
    provider = {key: coverage([(row["state"], row["provider"][key]) for row in selected]) for key in FIELDS}
    rework = dict.fromkeys(REWORK, 0)
    unknown = 0
    for row in selected:
        taxonomy = row["provider"]["rework_taxonomy"]
        if taxonomy is None:
            unknown += 1
        else:
            rework[taxonomy["rework_path"]] += 1
    provider["rework_paths"] = {"observed_group_count": sum(rework.values()),
        "unknown_group_count": unknown, "counts": rework}
    count = base["accepted_behavior_count"]
    gate = (base["denominator_state"] == "COMPLETE_RETAINED_WINDOW"
            and base["costs"]["allocation_state"] == "COMPLETE" and count > 0 and not global_conflict)
    def rate(bucket, denominator):
        full = value(bucket["full_total"])
        return rational(full / denominator) if gate and full is not None and denominator else None
    rates = {
        "active_seconds_per_accepted_behavior": rate(timing["active_seconds"], count),
        "accepted_behaviors_per_elapsed_day": rational(Fraction(count * 86400) / calendar) if gate and calendar else None,
        "provider_chain_first_pass_rate": rate(provider["first_pass"], provider["first_pass"]["observed_group_count"]),
        "successful_corrections_per_accepted_behavior": rate(provider["successful_correction_rounds"], count),
        "blocked_attempt_rate": rate(provider["failed_attempts"], value(provider["attempts"]["full_total"])),
        "repeated_blocked_chain_rate": rate(provider["repeated_blocked"], provider["repeated_blocked"]["observed_group_count"]),
        "retry_rate": None, "same_cause_failure_rate": None, "review_escape_rate": None}
    limits = {"RETAINED_WINDOW_OBSERVATIONS_ONLY", "UTC_ENDPOINTS_NOT_CLOCK_AUTHORITY",
              "NO_TYPED_RETRY_CAUSE_ESCAPE_EVIDENCE"}
    if global_conflict:
        limits.add("CONFLICTING_MEASUREMENT_ORDINALS")
    if overlaps:
        limits.add("OVERLAPPING_OBSERVATIONS")
    if any(bucket["full_total"] is None for bucket in
           [timing[key + "_seconds"] for key in KINDS] + [provider[key] for key in FIELDS]):
        limits.add("INCOMPLETE_MEASUREMENT_COVERAGE")
    return {"timing": timing, "provider": provider, "rates": rates, "rows": rows,
        "coverage": {"selected_group_count": len(selected),
            "selected_raw_ordinal_count": sum(len(row["metrics_ordinals"]) for row in selected),
            "conflict_group_count": sum(row["state"] == "CONFLICT" for row in rows),
            "unsupported_rates": {key: {"observed_group_count": 0, "unknown_group_count": len(selected)}
                for key in ("retry_rate", "same_cause_failure_rate", "review_escape_rate")}},
        "limitations": sorted(limits)}


def collect_measurements(repo, monkeypatch, window=None):
    with read_counter(monkeypatch) as observed:
        value = subject.summarize_accepted_work_measurements(repo.root, window=window)
    return value, observed


def whole_measurements(repo, value, observed, window=None, *, conflict=False, overlaps=False):
    expected = report_oracle(repo, observed, window)
    expected.update(schema_version=2, state="OBSERVATIONAL_ACCEPTED_WORK_MEASUREMENTS")
    expected["measurements"] = golden_measurements(repo, expected, global_conflict=conflict, overlaps=overlaps)
    assert value == expected
    assert len(value) == 17 and json.loads(json.dumps(value, allow_nan=False)) == value
    assert PRIVATE not in json.dumps(value)


@pytest.mark.parametrize("form,linked", [("inline", False), ("recovery", False), ("login", True)])
def test_whole_real_three_forms_fractional_active_and_literal_rates(tmp_path, monkeypatch, form, linked):
    repo = ReportRepository(tmp_path / "repo", form, linked)
    annotate(repo.specs[0], duration=Fraction(1, 8))
    repo.save_metrics()
    value, observed = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, observed)
    measurements = value["measurements"]
    assert measurements["timing"]["active_seconds"]["full_total"] == rational(Fraction(1, 8))
    assert measurements["rates"]["active_seconds_per_accepted_behavior"] == rational(Fraction(1, 8))
    assert measurements["rates"]["provider_chain_first_pass_rate"] == rational(1)
    assert measurements["rates"]["blocked_attempt_rate"] == rational(0)
    assert measurements["rates"]["retry_rate"] is None and measurements["rates"]["review_escape_rate"] is None
    assert measurements["timing"]["calendar_seconds"] is None
    # Historical v1 is still the exact old whole wire, even with new raw fields.
    with read_counter(monkeypatch) as wire:
        v1 = subject.summarize_accepted_work(repo.root)
    whole(repo, v1, wire)
    assert v1["schema_version"] == 1 and v1["timing"]["active_seconds"] is None


def test_failed_corrections_repeated_blocked_and_unknown_root_quality_are_distinct(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    repo.specs[0] = cost_record(repo.views[0], pair=(4, 4))
    annotate(repo.specs[0], first=False, counts=(4, 2, 2, 0, 1), path="REVIEWER_CHANGES_REQUIRED")
    failed = cost_record(repo.views[0], "failed-before-success", state="BLOCKED", finished=80, pair=(3, 3))
    annotate(failed, start=70, end=80, mono=1, first=None, counts=(3, 2, 1, 2, 0),
             path="ATTEMPT_BLOCKED", initial="ATTEMPT_BLOCKED", terminal="ATTEMPT_BLOCKED")
    repo.specs.append(failed)
    repo.save_metrics()
    value, observed = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, observed)
    rates = value["measurements"]["rates"]
    assert value["accepted_behavior_count"] == 1
    assert rates["active_seconds_per_accepted_behavior"] == rational(20)
    assert rates["successful_corrections_per_accepted_behavior"] == rational(1)
    assert rates["blocked_attempt_rate"] == rational(Fraction(2, 7))
    assert rates["repeated_blocked_chain_rate"] == rational(Fraction(1, 2))
    assert rates["provider_chain_first_pass_rate"] is None and rates["same_cause_failure_rate"] is None


def test_every_raw_ordinal_dedup_and_detachment_without_new_owned_reads(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    annotate(repo.specs[0])
    repo.specs += [copy.deepcopy(repo.specs[0]), copy.deepcopy(repo.specs[0])]
    repo.save_metrics()
    value, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, wire)
    assert value["measurements"]["rows"][0]["metrics_ordinals"] == [0, 1, 2]
    assert value["measurements"]["timing"]["active_seconds"]["full_total"] == rational(10)
    assert value["measurements"]["coverage"]["selected_raw_ordinal_count"] == 3
    saved = copy.deepcopy(value)
    value["measurements"]["provider"]["rework_paths"]["counts"].clear()
    value["measurements"]["rows"][0]["timing_states"].clear()
    later, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, later, wire)
    assert saved == later
    assert len(later["snapshot"]["files"]) == len(saved["snapshot"]["files"])


def test_real_shared_allocation_uses_same_proof_owner_and_denominator(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    second = repo.add_work(2)
    annotate(repo.specs[0])
    annotate(repo.specs[1], start=100, end=102, mono=20, duration=2)
    repo.declare_shared(entries=[{"cost_row_sha256": digest(canonical(repo.specs[0]["normalized"])),
        "metrics_ordinals": [0], "work_fingerprints": sorted((repo.views[0].work, second.work))}])
    value, observed = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, observed)
    assert value["costs"]["rows"][0]["allocation"] == "SHARED"
    assert value["measurements"]["rates"]["active_seconds_per_accepted_behavior"] == rational(6)
    assert len(value["proofs"]["works"]) == value["accepted_behavior_count"] == 2


@pytest.mark.parametrize("window", [{"start_utc": 80, "end_utc": 110}, {"start_utc": 100, "end_utc": 110}])
def test_explicit_calendar_keeps_waits_and_never_prorates_crossing_active(tmp_path, monkeypatch, window):
    repo = ReportRepository(tmp_path / "repo")
    annotate(repo.specs[0])
    if window["start_utc"] == 100:
        declared_state(repo.specs[0], "active", "WINDOW_BOUNDARY_UNVERIFIED")
    repo.save_metrics()
    value, observed = collect_measurements(repo, monkeypatch, window)
    whole_measurements(repo, value, observed, window)
    measurements = value["measurements"]
    assert measurements["timing"]["calendar_seconds"] == rational(window["end_utc"] - window["start_utc"])
    assert measurements["rates"]["accepted_behaviors_per_elapsed_day"] == rational(
        Fraction(86400, window["end_utc"] - window["start_utc"]))
    assert measurements["rates"]["active_seconds_per_accepted_behavior"] == (
        rational(10) if window["start_utc"] == 80 else None)
    assert measurements["timing"]["quota_wait_seconds"]["full_total"] is None


def test_complete_disjoint_closed_pairs_are_additive_but_open_and_history_are_unknown(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    annotate(repo.specs[0])
    raw = repo.specs[0]["raw"]["execution_observation"]
    raw["quota_wait"] = raw_pair(100, 110, 15, 10)
    raw["planned_stop"] = raw_pair(110, 120, 25, 10)
    for kind in ("quota_wait", "planned_stop"):
        declared_state(repo.specs[0], kind, "OBSERVED", 10)
    repo.save_metrics()
    value, observed = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, observed)
    assert all(value["measurements"]["timing"][kind + "_seconds"]["full_total"] == rational(10) for kind in KINDS)
    raw["planned_stop"] = raw_pair(110, None, 25)
    declared_state(repo.specs[0], "planned_stop", "OPEN_INTERVAL")
    repo.save_metrics()
    later, observed = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, later, observed)
    assert later["measurements"]["timing"]["planned_stop_seconds"]["full_total"] is None
    # A declared None interval is unknown, not a verified zero wait.
    raw["planned_stop"] = None
    declared_state(repo.specs[0], "planned_stop", "UNOBSERVED")
    repo.save_metrics()
    later, observed = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, later, observed)


def test_revisions_count_once_and_old_alias_time_does_not_create_progress(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    add_revision(repo, original_time=80, revision_time=105)
    annotate(repo.specs[0], start=70, end=80, mono=1)
    annotate(repo.specs[1], start=100, end=105, mono=20, duration=5)
    repo.save_metrics()
    window = {"start_utc": 90, "end_utc": 110}
    declared_state(repo.specs[0], "active", "WINDOW_BOUNDARY_UNVERIFIED")
    value, observed = collect_measurements(repo, monkeypatch, window)
    whole_measurements(repo, value, observed, window)
    assert value["accepted_behavior_count"] == 0 and len(value["acceptance_events"]) == 2
    assert all(rate is None for rate in value["measurements"]["rates"].values())


def test_same_reached_monotonic_arithmetic_mutant_fails_whole_oracle_then_restores(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    annotate(repo.specs[0], duration=Fraction(1, 8))
    repo.save_metrics()
    actual, reached = subject.Fraction, []
    def wrong(numerator=0, denominator=None):
        if denominator == NS:
            reached.append(True)
            denominator *= 2
        return actual(numerator) if denominator is None else actual(numerator, denominator)
    good, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, good, wire)
    with monkeypatch.context() as patcher:
        patcher.setattr(subject, "Fraction", wrong)
        broken, wire = collect_measurements(repo, monkeypatch)
        assert reached
        with pytest.raises(AssertionError):
            whole_measurements(repo, broken, wire)
    restored, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, restored, wire)
