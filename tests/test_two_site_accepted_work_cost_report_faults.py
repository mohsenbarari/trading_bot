"""Window, membership and bounded-public-exit faults over genuine retained chains."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import ast
from contextlib import contextmanager
import copy
from fractions import Fraction
import itertools
import json
import math
from pathlib import Path
import traceback

import pytest

from scripts import two_site_accepted_work_evidence as files
from tests.two_site_accepted_work_cost_support import (
    subject, custody, ReportRepository, cost_record, malformed_record, collect, whole,
    rational, amount, groups, legacy_cost, report_oracle, canonical, raw, digest,
    METRICS, LEDGER, INVENTORY, MEMBERSHIP, MEMBERSHIP_PATH, PRIVATE,
)
from tests.test_two_site_accepted_work_proofs import revision
from tests.test_two_site_work_cost_report import report as old_report


def fixed(code, operation):
    with pytest.raises(subject.AcceptedWorkCostReportError) as caught:
        operation()
    error = caught.value
    assert type(error) is subject.AcceptedWorkCostReportError
    assert error.code == code and str(error) == code and error.__suppress_context__
    assert PRIVATE not in "".join(traceback.format_exception(error))
    return error


def masses(value, prefix=()):
    """Independently flatten only additive quantities, not duplicate full-value views."""
    result = {}
    for key, item in value.items():
        if key in ("full_usage", "full_total"):
            continue
        if type(item) is dict and set(item) == {"numerator", "denominator"}:
            numerator, denominator = item["numerator"], item["denominator"]
            assert type(numerator) is int and numerator >= 0
            assert type(denominator) is int and denominator > 0
            assert math.gcd(numerator, denominator) == 1
            result[prefix + (key,)] = Fraction(numerator, denominator)
        elif type(item) is dict:
            result.update(masses(item, prefix + (key,)))
        else:
            raise AssertionError((prefix, key, type(item)))
    return result


def conservation(value):
    costs = value["costs"]
    def equal_sum(total, components):
        expected = masses(costs[total])
        parts = [masses(component) for component in components]
        assert all(set(part) == set(expected) for part in parts)
        assert expected == {key: sum((part[key] for part in parts), Fraction()) for key in expected}
    equal_sum("retained_totals", [costs[key] for key in ("totals", "excluded", "unknown_time")])
    equal_sum("totals", [costs[key] for key in ("direct", "shared", "unallocated")])
    allocated = [masses(item["costs"]) for item in costs["per_work"]]
    direct, shared = masses(costs["direct"]), masses(costs["shared"])
    assert {key: direct[key] + shared[key] for key in direct} == {
        key: sum((part[key] for part in allocated), Fraction()) for key in direct}
    for bucket in [costs[key] for key in ("totals", "retained_totals", "direct", "shared", "unallocated", "excluded", "unknown_time")]:
        c, p = bucket["codex"]["observed_subtotal"], bucket["provider"]["observed_subtotal"]
        number = lambda item: Fraction(item["numerator"], item["denominator"])
        assert number(c["input_tokens"]) == number(c["cached_input_tokens"]) + number(c["uncached_input_tokens"])
        assert number(p["total_tokens"]) == number(p["input_tokens"]) + number(p["output_tokens"])
    ordinals = [ordinal for row in costs["rows"] for ordinal in row["metrics_ordinals"]]
    assert sorted(ordinals) == list(range(costs["input_record_count"]))
    assert len(ordinals) == len(set(ordinals))


@pytest.mark.parametrize("finished", [99, 100, 109, 110, 100.5, None, "yesterday", True, [], -1, 10 ** 12 + 1])
def test_half_open_windows_unknown_timestamp_buckets_and_whole_literal(tmp_path, monkeypatch, finished):
    repo = ReportRepository(tmp_path / "repo")
    repo.specs[0] = cost_record(repo.views[0], finished=finished)
    repo.save_metrics()
    window = {"start_utc": 100, "end_utc": 110}
    value, observed = collect(repo, monkeypatch, window)
    whole(repo, value, observed, window)
    conservation(value)
    assert value["timing"]["active_seconds"] is None
    assert value["timing"]["quota_wait_seconds"] is None and value["timing"]["calendar_seconds"] is None
    if type(finished) not in (int, float) or type(finished) is bool or not 0 <= finished <= 10 ** 12:
        assert value["window"]["unknown_time_ordinals"] == [0]
        assert value["costs"]["unknown_time"]["codex"]["observed_subtotal"]["uncached_input_tokens"] == rational(20)
        assert value["accepted_behavior_count"] == 0
        assert value["token_per_accepted_behavior"] == {"codex": None, "provider": None}


def test_bound_failed_alias_timestamp_is_not_an_acceptance_event(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    repo.specs[0] = cost_record(repo.views[0], finished=50)
    repo.specs.append(cost_record(repo.views[0], "late-failed-alias", state="BLOCKED", finished=105,
                                  codex=(200, 100, 100, 20), provider=(10, 10, 20), seconds=7))
    # Existing controller phases overlap/omit setup. They are not additive active time.
    repo.specs[1]["raw"]["controller_phase_seconds"] = {
        "repository_validation": 1, "task_context": 2, "codex_readiness": 3,
        "coordinator": 50, "post_coordinator_validation": 4}
    repo.save_metrics()
    window = {"start_utc": 100, "end_utc": 110}
    value, observed = collect(repo, monkeypatch, window)
    whole(repo, value, observed, window)
    conservation(value)
    assert len(value["proofs"]["run_bindings"]) == 2
    assert all(binding["state"] == "BOUND" for binding in value["proofs"]["run_bindings"])
    assert len(value["acceptance_events"]) == 1 and value["acceptance_events"][0]["controller_run_id"] == repo.controller
    assert value["accepted_behavior_count"] == value["handler_scenario_count"] == 0
    assert value["costs"]["unallocated"]["codex"]["observed_subtotal"]["uncached_input_tokens"] == rational(100)


def add_revision(repo, *, original_time=90, revision_time=105):
    first = repo.views[0]
    second = revision(first)
    repo.head, repo.ledger, repo.index = second.head, second.ledger, second.index
    repo.commit_bodies.update(second.commit_bodies)
    repo.artifacts.update(second.artifacts)
    repo.views.append(second)
    repo.specs = [cost_record(first, finished=original_time), cost_record(second, finished=revision_time)]
    repo.save_metrics()
    return second


def test_first_retained_acceptance_not_later_source_revision_or_unknown_predecessor(tmp_path, monkeypatch):
    for index, (original_time, later_time) in enumerate(((90, 105), (None, 105), (100, None), (100, 100))):
        repo = ReportRepository(tmp_path / f"case-{index}")
        second = add_revision(repo, original_time=original_time, revision_time=later_time)
        assert repo.views[0].work == second.work
        assert repo.views[0].identity["fingerprint"] != second.identity["fingerprint"]
        value, observed = collect(repo, monkeypatch, {"start_utc": 100, "end_utc": 110})
        whole(repo, value, observed, {"start_utc": 100, "end_utc": 110})
        conservation(value)
        assert len(value["proofs"]["works"]) == 1 and len(value["acceptance_events"]) == 2
        selected = int(original_time == later_time == 100)
        assert value["accepted_behavior_count"] == selected and value["handler_scenario_count"] == 2 * selected
        assert value["costs"]["unallocated"]["row_weight"] == rational(1 - selected)
        if not selected:
            assert value["token_per_accepted_behavior"] == {"codex": None, "provider": None}
        if original_time is None or later_time is None:
            assert "UNKNOWN_WINDOW_TIMESTAMPS" in value["limitations"]
            assert value["denominator_state"] == "PARTIAL_RETAINED_WINDOW"


def test_full_history_conflict_outside_window_cannot_be_filtered_into_acceptance(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    alias = cost_record(repo.views[0], "outside-alias", finished=10)
    contradicted = copy.deepcopy(alias)
    contradicted["raw"]["proposal_sha256"] = "a" * 64
    contradicted["normalized"]["dimensions"]["proposal_sha256"] = "a" * 64
    repo.specs.extend((alias, contradicted))
    repo.save_metrics()
    value, _ = collect(repo, monkeypatch, {"start_utc": 100, "end_utc": 110})
    assert value["proofs"]["works"] == [] and value["proofs"]["handler_scenarios"] == []
    assert value["accepted_behavior_count"] == value["handler_scenario_count"] == 0
    assert value["acceptance_events"] == [] and value["denominator_state"] == "PARTIAL_RETAINED_WINDOW"
    assert value["costs"]["retained_totals"] == amount([(spec, 1) for spec in repo.specs])
    assert value["costs"]["totals"] == value["costs"]["unallocated"] == amount([(repo.specs[0], 1)])
    assert value["costs"]["excluded"] == amount([(spec, 1) for spec in repo.specs[1:]])
    assert all(binding["state"] == "CONFLICT" for binding in value["proofs"]["run_bindings"])
    conservation(value)


def test_malformed_null_unsafe_id_and_zero_sign_duplicates_retain_all_ordinals(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    zero = cost_record(repo.views[0], codex=(0, 0, 0, 0), provider=(0, 0, 0), seconds=-0.0, finished=-0.0)
    plus = cost_record(repo.views[0], codex=(0, 0, 0, 0), provider=(0, 0, 0), seconds=0.0, finished=0.0)
    null = cost_record(run="unused", codex=None, provider=None, pair=None, seconds=None, finished=None)
    null["raw"]["run_id"] = null["normalized"]["run_id"] = None
    unsafe = copy.deepcopy(null)
    unsafe["raw"]["run_id"] = "unsafe/" + PRIVATE
    repo.specs = [zero, plus, null, copy.deepcopy(null), unsafe, malformed_record(None), malformed_record([PRIVATE])]
    repo.save_metrics()
    value, observed = collect(repo, monkeypatch)
    whole(repo, value, observed)
    conservation(value)
    assert value["costs"]["distinct_record_count"] == 6 and value["costs"]["duplicate_record_count"] == 1
    assert value["costs"]["rows"][0]["metrics_ordinals"] == [0, 1]
    assert value["costs"]["rows"][0]["cost_row_sha256"] == digest(canonical(zero["normalized"]))
    assert value["costs"]["totals"]["codex"]["unknown_weight"] == rational(5)
    assert value["costs"]["totals"]["codex"]["full_usage"] is None
    assert PRIVATE not in json.dumps(value)


def test_failed_partial_zero_observation_is_not_free_or_full_usage(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    failed = cost_record(repo.views[0], "failed-partial", state="BLOCKED", codex=None,
                         provider=(0, 0, 0), pair=(1, 0), seconds=None, partial=True)
    repo.specs.append(failed)
    repo.save_metrics()
    value, observed = collect(repo, monkeypatch)
    whole(repo, value, observed)
    conservation(value)
    provider = value["costs"]["totals"]["provider"]
    assert provider["observed_weight"] == rational(2) and provider["unknown_weight"] == rational(0)
    assert provider["full_usage"] is None and provider["unknown_attempt_count"] == rational(1)
    assert value["costs"]["direct"]["row_weight"] == rational(2)
    assert value["token_per_accepted_behavior"] == {"codex": None, "provider": None}


@pytest.mark.parametrize("fault,reason", [
    ("version", "UNSUPPORTED_SCHEMA"), ("extra", "MALFORMED_MEMBERSHIP"),
    ("metrics", "HASH_MISMATCH"), ("ledger", "HASH_MISMATCH"),
    ("row-sha", "ROW_MISMATCH"), ("ordinals", "ROW_MISMATCH"),
    ("member", "MEMBER_MISMATCH"), ("single-member", "MALFORMED_MEMBERSHIP"),
    ("duplicate-entry", "CONFLICTING_MEMBERSHIP"), ("early-anchor", "MEMBER_MISMATCH"),
])
def test_bad_committed_optional_membership_never_partially_allocates(tmp_path, monkeypatch, fault, reason):
    repo = ReportRepository(tmp_path / "repo")
    repo.add_work(2)
    repo.specs.append(cost_record(run="shared-overhead", codex=(3, 0, 3, 1), provider=(2, 1, 3)))
    def corruption(document):
        entry = document["memberships"][0]
        if fault == "version":
            document["schema_version"] = "unsupported-v2"
        elif fault == "extra":
            document["accepted"] = True
        elif fault in ("metrics", "ledger"):
            document["metrics_sha256" if fault == "metrics" else "canonical_ledger_sha256"] = "0" * 64
        elif fault == "row-sha":
            entry["cost_row_sha256"] = "0" * 64
        elif fault == "ordinals":
            entry["metrics_ordinals"] = [2000]
        elif fault == "member":
            entry["work_fingerprints"] = sorted([repo.work, "f" * 64])
        elif fault == "single-member":
            entry["work_fingerprints"] = [repo.work]
        elif fault == "duplicate-entry":
            document["memberships"].append(copy.deepcopy(entry))
        else:
            document["anchor_commit"] = repo.views[0].parent
    repo.declare_shared(transform=corruption)
    value, _ = collect(repo, monkeypatch)
    assert value["accepted_behavior_count"] == 2 and value["handler_scenario_count"] == 4
    assert len(value["proofs"]["works"]) == 2
    assert value["costs"]["shared"] == amount([])
    assert value["costs"]["direct"] == amount([(spec, 1) for spec in repo.specs[:2]])
    assert value["costs"]["unallocated"] == amount([(repo.specs[2], 1)])
    assert value["costs"]["membership"] == {
        "state": "UNSUPPORTED_MEMBERSHIP" if fault == "version" else "INVALID_MEMBERSHIP",
        "reason_code": reason, "receipt_sha256": digest(repo.membership_body),
        "anchor_commit": None if fault in ("version", "extra") else
                         repo.views[0].parent if fault == "early-anchor" else repo.membership_anchor,
        "membership_count": 0}
    assert value["costs"]["allocation_state"] == "PARTIAL"
    assert value["token_per_accepted_behavior"] == {"codex": None, "provider": None}
    conservation(value)


def test_shared_members_outside_selected_denominator_do_not_redistribute(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    repo.add_work(2)
    repo.specs[1] = cost_record(repo.views[1], finished=50)
    repo.specs.append(cost_record(run="shared-overhead", finished=105))
    repo.declare_shared()
    window = {"start_utc": 100, "end_utc": 110}
    value, observed = collect(repo, monkeypatch, window)
    whole(repo, value, observed, window)
    conservation(value)
    assert value["costs"]["membership"]["state"] == "VERIFIED"
    assert value["accepted_behavior_count"] == 1
    assert value["costs"]["shared"] == amount([])
    assert value["costs"]["unallocated"] == amount([(repo.specs[-1], 1)])
    assert "WINDOW_MEMBERSHIP_OUTSIDE_DENOMINATOR" in value["limitations"]


def test_membership_live_git_mismatch_nonancestor_and_unsafe_are_not_positive(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    repo.add_work(2)
    repo.specs.append(cost_record(run="shared-overhead"))
    repo.declare_shared()
    original = repo.membership_body
    repo.put(MEMBERSHIP_PATH, original + b" ")
    value, _ = collect(repo, monkeypatch)
    assert value["costs"]["membership"]["state"] == "INVALID_MEMBERSHIP"
    assert value["costs"]["membership"]["reason_code"] == "HASH_MISMATCH"
    assert value["costs"]["shared"] == amount([])
    conservation(value)
    repo.put(MEMBERSHIP_PATH, original)
    tree = repo.run("rev-parse", "HEAD^{tree}").strip().decode()
    sibling = repo.run("commit-tree", tree, "-p", repo.head, input=b"parallel nonancestor\n").strip().decode()
    repo.declare_shared(transform=lambda document: document.update(anchor_commit=sibling))
    value, _ = collect(repo, monkeypatch)
    assert value["costs"]["membership"]["reason_code"] == "ANCHOR_MISMATCH"
    assert value["costs"]["membership"]["anchor_commit"] is None
    path = repo.root / MEMBERSHIP_PATH
    path.unlink()
    target = repo.root / "untrusted-membership.json"
    target.write_bytes(original)
    before = target.stat()
    path.symlink_to(target)
    fixed("UNSAFE_FILE", lambda: subject.summarize_accepted_work(repo.root))
    assert target.read_bytes() == original and target.stat().st_ino == before.st_ino


def test_safe_run_cost_variants_remain_charged_and_legacy_conflict_is_preserved(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    repo.specs.append(cost_record(repo.views[0], codex=(200, 100, 100, 20), provider=(100, 20, 120)))
    repo.save_metrics()
    value, _ = collect(repo, monkeypatch)
    cost = legacy_cost(repo.specs)
    assert value["costs"]["legacy_observation"] == old_report(2, 2, cost, runs=1, conflicts=1,
        unattributed=(2, 2, 0), unknown_cost=cost)
    assert value["costs"]["distinct_record_count"] == 2 and value["costs"]["duplicate_record_count"] == 0
    assert value["costs"]["totals"] == value["costs"]["unallocated"] == amount([(spec, 1) for spec in repo.specs])
    assert value["costs"]["direct"] == amount([])
    assert value["accepted_behavior_count"] == 1 and value["costs"]["allocation_state"] == "PARTIAL"
    conservation(value)


@pytest.mark.parametrize("window", [False, 1, [], {}, {"start_utc": 0},
    {"start_utc": True, "end_utc": 2}, {"start_utc": 0.0, "end_utc": 2},
    {"start_utc": 1, "end_utc": 1}, {"start_utc": -1, "end_utc": 2},
    {"start_utc": 0, "end_utc": 10 ** 12 + 1}, {"start_utc": 0, "end_utc": 2, "extra": 3}])
def test_invalid_window_is_fixed_before_any_root_io(monkeypatch, window):
    opened = []
    def forbidden(*args, **kwargs):
        opened.append(True)
        raise AssertionError(PRIVATE)
    with monkeypatch.context() as patcher:
        patcher.setattr(subject, "_proof_snapshot", forbidden)
        fixed("INVALID_WINDOW", lambda: subject.summarize_accepted_work("/not-a-fixture", window=window))
    assert opened == []


@pytest.mark.parametrize("fault,code", [("deadline", "TIME_LIMIT"), ("metadata", "COUNT_LIMIT"),
    ("bytes", "BYTE_LIMIT"), ("final-custody", "SNAPSHOT_DRIFT"), ("final-close", "UNSAFE_FILE"),
    ("output", "BYTE_LIMIT")])
def test_public_same_owner_fatal_exit_never_returns_partial_report(tmp_path, monkeypatch, fault, code):
    repo = ReportRepository(tmp_path / "repo")
    if fault == "metadata":
        repo.add_work(2)
        repo.specs.append(cost_record(run="shared-overhead"))
        repo.declare_shared()
    actual_factory, actual_run = custody._proof_snapshot, subject._Report.run
    owners, returned, reached, owned_fds = [], [], [], []
    @contextmanager
    def tracked(root):
        manager = actual_factory(root)
        with manager as snapshot:
            owners.append(snapshot)
            if fault == "bytes":
                for index in range(4):
                    name = f"budget-{index}.json"
                    repo.put(INVENTORY + name, b" " * 1998000)
                    snapshot._read("receipt", name)
                reached.append(True)
            yield snapshot
    with monkeypatch.context() as patcher:
        patcher.setattr(subject, "_proof_snapshot", tracked)
        if fault == "deadline":
            actual_groups, actual_clock = subject._Report._groups, custody.time.monotonic
            expired = []
            def grouping(report, records):
                result = actual_groups(report, records)
                reached.append(True)
                expired.append(report.owner._deadline)
                return result
            patcher.setattr(subject._Report, "_groups", grouping)
            patcher.setattr(custody.time, "monotonic", lambda: expired[0] if expired else actual_clock())
        elif fault == "metadata":
            actual_charge = files._EvidenceFiles._charge_metadata
            def charge(owner, key, count):
                actual_charge(owner, key, count)
                if key[0] == "shared-membership":
                    reached.append(True)
                    actual_charge(owner, ("test-hard-metadata-boundary",), 65537)
            patcher.setattr(files._EvidenceFiles, "_charge_metadata", charge)
        elif fault in ("final-custody", "final-close"):
            def running(report):
                value = actual_run(report)
                owned_fds.extend(report.owner._handles)
                returned.append(True)
                return value
            patcher.setattr(subject._Report, "run", running)
            if fault == "final-custody":
                actual_verify = custody._ProofSnapshot._verify
                def verify(snapshot):
                    if returned:
                        reached.append(True)
                        raise custody._EvidenceSnapshotError("SNAPSHOT_DRIFT")
                    return actual_verify(snapshot)
                patcher.setattr(custody._ProofSnapshot, "_verify", verify)
            else:
                actual_close = os.close
                def close(descriptor):
                    actual_close(descriptor)
                    if returned and not reached and descriptor in owned_fds:
                        reached.append(True)
                        raise OSError(PRIVATE)
                patcher.setattr(files.os, "close", close)
        elif fault == "output":
            actual_canonical = subject._canonical
            def serializing(value):
                if type(value) is dict and value.get("state") == "OBSERVATIONAL_ACCEPTED_WORK_COSTS":
                    reached.append(True)
                    return " " * 2000001
                return actual_canonical(value)
            patcher.setattr(subject, "_canonical", serializing)
        error = fixed(code, lambda: subject.summarize_accepted_work(repo.root))
        assert reached and owners
        assert error.__suppress_context__ and all(owner._state == "CLOSED" for owner in owners)
        for owner in owners:
            with pytest.raises(custody._EvidenceSnapshotError) as closed:
                owner._metadata()
            assert closed.value.code == "CLOSED_SNAPSHOT"
        for descriptor in owned_fds:
            with pytest.raises(OSError):
                os.fstat(descriptor)


def test_candidate_byte_bootstrap_and_unchanged_legacy_boundaries():
    directory = Path(__file__).parent
    for name in ("test_two_site_accepted_work_cost_report.py", "test_two_site_accepted_work_cost_report_faults.py"):
        body = (directory / name).read_bytes()
        assert 0 < len(body) <= 24000
        assert body.endswith(b"\n") and not body.endswith(b"\n\n")
        assert all(line.rstrip() == line for line in body.splitlines())
        tree = ast.parse(body)
        app_imports = [node.lineno for node in tree.body if isinstance(node, ast.ImportFrom) and
                       node.module and node.module.startswith("scripts")]
        fences = [node.lineno for node in tree.body if isinstance(node, ast.Expr) and
                  isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and
                  node.value.func.id == "install_p1_00_network_fence"]
        assert len(fences) == 1 and (not app_imports or fences[0] < min(app_imports))
    support = (directory / "two_site_accepted_work_cost_support.py").read_bytes()
    assert 0 < len(support) <= 32000
    assert subject.summarize_work_lineage_ledger([]) == old_report(0, 0)


@pytest.mark.parametrize("body", [b'[NaN]', b'[Infinity]', b'[{"finished_at":1e999}]'])
def test_nonfinite_json_is_a_fatal_ledger_not_an_unknown_time_success(tmp_path, body):
    repo = ReportRepository(tmp_path / "repo")
    repo.artifact(METRICS, body)
    fixed("INVALID_METRICS_LEDGER", lambda: subject.summarize_accepted_work(repo.root))
