"""Full public retained-work integration and reached arithmetic guard oracles."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
from fractions import Fraction
import itertools

import pytest

from tests.two_site_accepted_work_cost_support import (
    subject, ReportRepository, cost_record, collect, whole, rational,
    MEMBERSHIP_PATH, PRIVATE, METRICS, UTC_LITERALS, amount, expected_legacy,
    canonical, digest, groups,
)


@pytest.mark.parametrize("form,linked", [("inline", False), ("recovery", False), ("login", True)])
def test_public_whole_three_real_forms_failed_costs_dedup_and_detachment(tmp_path, monkeypatch, form, linked):
    repo = ReportRepository(tmp_path / "repo", form, linked)
    repo.specs += [cost_record(repo.views[0], "failed-attempt", state="BLOCKED", codex=(30, 10, 20, 3),
                              provider=(9, 3, 12), seconds=4, finished=99), copy.deepcopy(repo.specs[0])]
    repo.specs[1]["raw"]["error"] = PRIVATE
    repo.save_metrics()
    value, observed = collect(repo, monkeypatch)
    whole(repo, value, observed)
    assert value["accepted_behavior_count"] == 1 and value["handler_scenario_count"] == 2
    assert value["costs"]["totals"]["codex"]["observed_subtotal"]["uncached_input_tokens"] == rational(40)
    assert value["costs"]["duplicate_record_count"] == 1
    retained = copy.deepcopy(value)
    value["costs"]["per_work"][0]["costs"]["provider"]["observed_subtotal"].clear()
    value["proofs"]["works"][0]["proof_refs"].clear()
    later, reads = collect(repo, monkeypatch)
    whole(repo, later, reads)
    assert retained["costs"] == later["costs"]


def test_real_committed_three_member_fraction_allocation_and_same_reached_mutation(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    repo.add_work(2)
    repo.add_work(3)
    repo.specs.append(cost_record(run="shared-overhead", codex=(1, 0, 1, 1), provider=(1, 0, 1),
                                  seconds=1, finished=103))
    declaration = repo.declare_shared()
    assert len({view.work for view in repo.views}) == 3
    assert repo.run("show", f"HEAD:{MEMBERSHIP_PATH}") == repo.membership_body
    assert repo.run("merge-base", "--is-ancestor", declaration["anchor_commit"], repo.head) == b""
    value, observed = collect(repo, monkeypatch)
    whole(repo, value, observed)
    assert value["costs"]["shared"]["row_weight"] == rational(1)
    for work in value["costs"]["per_work"]:
        assert work["costs"]["codex"]["observed_subtotal"]["uncached_input_tokens"] == rational(Fraction(61, 3))
    # Inject an actually reached arithmetic guard mutation, not an edited return dict.
    actual = subject.Fraction
    reached = []
    def biased(numerator=0, denominator=None):
        result = actual(numerator) if denominator is None else actual(numerator, denominator)
        if denominator == 3 and numerator == 1:
            reached.append(True)
            return actual(1, 2)
        return result
    with monkeypatch.context() as fault:
        fault.setattr(subject, "Fraction", biased)
        changed, reads = collect(repo, monkeypatch)
        assert reached
        with pytest.raises(AssertionError):
            whole(repo, changed, reads)
    restored, reads = collect(repo, monkeypatch)
    whole(repo, restored, reads)


def test_permuted_known_unknown_failed_rows_and_input_bytes_are_immutable(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    records = [repo.specs[0], cost_record(repo.views[0], "failed-without-codex", state="BLOCKED", codex=None),
               cost_record(run="unallocated-overhead", provider=None, pair=None)]
    expected_totals = None
    for permutation in itertools.permutations(records):
        repo.specs = list(permutation)
        repo.save_metrics()
        paths = set(repo.artifacts) | {"docs/refactor/two-server/section-1/00-stage-ledger.md", repo.receipt_path}
        image = {path: ((repo.root / path).read_bytes(), (repo.root / path).stat().st_ino,
                       (repo.root / path).stat().st_mode) for path in paths}
        value, observed = collect(repo, monkeypatch)
        whole(repo, value, observed)
        assert image == {path: ((repo.root / path).read_bytes(), (repo.root / path).stat().st_ino,
                               (repo.root / path).stat().st_mode) for path in paths}
        if expected_totals is None:
            expected_totals = copy.deepcopy(value["costs"]["totals"])
        assert value["costs"]["totals"] == expected_totals
        assert value["token_per_accepted_behavior"] == {"codex": None, "provider": None}


@pytest.mark.parametrize("timestamp", [*UTC_LITERALS, "1970-01-01T00:01:40", "1970-01-01T00:01:40+03:30",
    "1970-01-01T00:01:40+01:00", "1970-02-29T00:00:00Z", "1970-01-01T00:01:60Z",
    "1970-01-01T00:01:40.1234567Z", "1970-01-01t00:01:40z", "yesterday"])
def test_utc_iso_uses_literal_epoch_oracles_not_printable_string_guesses(tmp_path, monkeypatch, timestamp):
    repo = ReportRepository(tmp_path / "repo")
    repo.specs[0] = cost_record(repo.views[0], finished=timestamp)
    repo.save_metrics()
    window = {"start_utc": 100, "end_utc": 110}
    value, observed = collect(repo, monkeypatch, window)
    whole(repo, value, observed, window)
    literal = UTC_LITERALS.get(timestamp)
    assert value["acceptance_events"][0]["accepted_at_utc"] == (rational(literal) if literal is not None else None)
    assert value["accepted_behavior_count"] == int(literal is not None and 100 <= literal < 110)
    assert value["window"]["unknown_time_ordinals"] == ([] if literal is not None else [0])


def test_empty_retention_and_exact_two_thousand_rows_keep_unknown_cost_mass(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    original = repo.specs[0]
    repo.specs = []
    repo.save_metrics()
    empty, _ = collect(repo, monkeypatch)
    assert empty["costs"]["totals"] == empty["costs"]["retained_totals"] == amount([])
    assert empty["costs"]["legacy_observation"] == expected_legacy([])
    assert empty["accepted_behavior_count"] == empty["handler_scenario_count"] == 0
    assert empty["acceptance_events"] == [] and empty["costs"]["rows"] == []
    assert empty["denominator_state"] == "PARTIAL_RETAINED_WINDOW"
    assert empty["token_per_accepted_behavior"] == {"codex": None, "provider": None}
    unknown = cost_record(codex=None, provider=None, pair=None, seconds=None, finished=None)
    unknown["raw"]["run_id"] = unknown["normalized"]["run_id"] = None
    repo.specs = [original] + [copy.deepcopy(unknown) for _ in range(1999)]
    repo.save_metrics()
    value, reads = collect(repo, monkeypatch)
    whole(repo, value, reads)
    assert value["costs"]["distinct_record_count"] == 2000
    assert value["costs"]["totals"]["codex"]["unknown_weight"] == rational(1999)
    assert value["accepted_behavior_count"] == 1 and value["handler_scenario_count"] == 2
    repo.artifact(METRICS, [None] * 2001)
    with pytest.raises(subject.AcceptedWorkCostReportError) as overflow:
        subject.summarize_accepted_work(repo.root)
    assert overflow.value.code == str(overflow.value) == "INVALID_METRICS_LEDGER"


@pytest.mark.parametrize("mode", ["symlink", "tree", "gitlink"])
def test_live_regular_membership_cannot_downgrade_unsafe_committed_head_mode(tmp_path, monkeypatch, mode):
    repo = ReportRepository(tmp_path / "repo")
    repo.add_work(2)
    repo.specs.append(cost_record(run="shared-overhead"))
    repo.declare_shared()
    leaf, body = repo.root / MEMBERSHIP_PATH, repo.membership_body
    if mode == "symlink":
        leaf.unlink()
        leaf.symlink_to("not-membership-authority.json")
        repo.head = repo.commit("fixture HEAD symlink, never membership authority", [MEMBERSHIP_PATH])
        leaf.unlink()
    elif mode == "tree":
        leaf.unlink()
        leaf.mkdir(mode=0o700)
        child = leaf / "child.json"
        child.write_bytes(b'{"not_membership":true}\n')
        repo.head = repo.commit("fixture HEAD tree, never membership authority", [MEMBERSHIP_PATH])
        child.unlink()
        leaf.rmdir()
    else:
        repo.run("update-index", "--add", "--cacheinfo", "160000", repo.views[0].accepted, MEMBERSHIP_PATH)
        repo.run("commit", "-m", "fixture HEAD gitlink, never membership authority")
        repo.head = repo.run("rev-parse", "HEAD").strip().decode()
    repo.put(MEMBERSHIP_PATH, body)
    expected_mode = {"symlink": b"120000", "tree": b"040000", "gitlink": b"160000"}[mode]
    assert repo.run("ls-tree", "HEAD", "--", MEMBERSHIP_PATH).split(b" ", 1)[0] == expected_mode
    assert leaf.is_file() and not leaf.is_symlink() and leaf.read_bytes() == body
    assert leaf.stat().st_nlink == 1 and leaf.stat().st_uid == os.geteuid()
    actual_factory, owners, returned = subject._proof_snapshot, [], []
    def observed_owner(root):
        context = actual_factory(root)
        owners.append(context)
        return context
    with monkeypatch.context() as patcher:
        patcher.setattr(subject, "_proof_snapshot", observed_owner)
        with pytest.raises(subject.AcceptedWorkCostReportError) as caught:
            returned.append(subject.summarize_accepted_work(repo.root))
    assert caught.value.code == str(caught.value) == "UNSUPPORTED_SOURCE"
    assert caught.value.__suppress_context__ and returned == [] and len(owners) == 1
    assert owners[0]._state == owners[0]._files._state == "CLOSED"
    assert owners[0]._files._handles == set()
    with pytest.raises(subject._EvidenceSnapshotError) as closed:
        owners[0]._metadata()
    assert closed.value.code == "CLOSED_SNAPSHOT"


def test_explicit_membership_overrides_bound_direct_cost_once(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    second = repo.add_work(2)
    repo.declare_shared(entries=[{"cost_row_sha256": digest(canonical(repo.specs[0]["normalized"])),
        "metrics_ordinals": [0], "work_fingerprints": sorted(view.work for view in repo.views)}])
    value, observed = collect(repo, monkeypatch)
    whole(repo, value, observed)
    assert all(binding["state"] == "BOUND" for binding in value["proofs"]["run_bindings"])
    assert value["costs"]["rows"][0]["allocation"] == "SHARED"
    assert value["costs"]["direct"] == amount([(repo.specs[1], 1)])
    assert value["costs"]["shared"] == amount([(repo.specs[0], 1)])
    assert value["costs"]["unallocated"] == amount([])
    owners = {item["work_fingerprint"]: item["costs"] for item in value["costs"]["per_work"]}
    assert owners[repo.views[0].work] == amount([(repo.specs[0], Fraction(1, 2))])
    assert owners[second.work] == amount([(repo.specs[0], Fraction(1, 2)), (repo.specs[1], 1)])
    assert value["token_per_accepted_behavior"]["codex"]["uncached_input_tokens"] == rational(20)


def test_shared_duplicate_requires_exact_all_raw_ordinals_and_never_double_charges(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    repo.add_work(2)
    shared = cost_record(run="shared-overhead")
    repo.specs.extend((shared, copy.deepcopy(shared)))
    declaration = repo.declare_shared()
    assert declaration["memberships"][0]["metrics_ordinals"] == [2, 3]
    good, observed = collect(repo, monkeypatch)
    whole(repo, good, observed)
    assert good["costs"]["input_record_count"] == 4 and good["costs"]["distinct_record_count"] == 3
    assert good["costs"]["shared"] == amount([(shared, 1)])
    entry = copy.deepcopy(declaration["memberships"][0])
    entry["metrics_ordinals"] = [2]
    repo.declare_shared(entries=[entry])
    bad, _ = collect(repo, monkeypatch)
    assert bad["proofs"] == good["proofs"] and bad["acceptance_events"] == good["acceptance_events"]
    assert bad["costs"]["membership"]["state"] == "INVALID_MEMBERSHIP"
    assert bad["costs"]["membership"]["reason_code"] == "ROW_MISMATCH"
    assert bad["costs"]["shared"] == amount([]) and bad["costs"]["unallocated"] == amount([(shared, 1)])
    assert bad["costs"]["totals"] == amount([(group["spec"], 1) for group in groups(repo.specs)])
    assert bad["costs"]["rows"][-1]["metrics_ordinals"] == [2, 3]


def test_committed_declaration_missing_live_leaf_is_not_genuine_no_membership(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    repo.add_work(2)
    shared = cost_record(run="shared-overhead")
    repo.specs.append(shared)
    repo.declare_shared()
    body = repo.membership_body
    (repo.root / MEMBERSHIP_PATH).unlink()
    value, _ = collect(repo, monkeypatch)
    assert value["accepted_behavior_count"] == 2 and value["handler_scenario_count"] == 4
    assert value["costs"]["membership"] == {"state": "INVALID_MEMBERSHIP", "reason_code": "HASH_MISMATCH",
        "receipt_sha256": None, "anchor_commit": None, "membership_count": 0}
    assert "SHARED_MEMBERSHIP_UNVERIFIED" in value["limitations"]
    assert "NO_RETAINED_MEMBERSHIP" not in value["limitations"]
    assert value["costs"]["shared"] == amount([]) and value["costs"]["unallocated"] == amount([(shared, 1)])
    assert value["token_per_accepted_behavior"] == {"codex": None, "provider": None}
    repo.put(MEMBERSHIP_PATH, body)
    restored, observed = collect(repo, monkeypatch)
    whole(repo, restored, observed)


@pytest.mark.parametrize("fault", ["exclude-bound", "conflicting-cost-run"])
def test_membership_cannot_launder_bound_exclusion_or_ambiguous_cost_run(tmp_path, monkeypatch, fault):
    repo = ReportRepository(tmp_path / "repo")
    repo.add_work(2)
    if fault == "exclude-bound":
        repo.add_work(3)
        ordinal = 0
        members = [view.work for view in repo.views[1:]]
    else:
        repo.specs.extend((cost_record(repo.views[0], "ambiguous-cost", state="BLOCKED"),
            cost_record(repo.views[0], "ambiguous-cost", state="BLOCKED", codex=(200, 80, 120, 20))))
        ordinal = 2
        members = [view.work for view in repo.views]
    repo.declare_shared(entries=[{"cost_row_sha256": digest(canonical(repo.specs[ordinal]["normalized"])),
        "metrics_ordinals": [ordinal], "work_fingerprints": sorted(members)}])
    value, _ = collect(repo, monkeypatch)
    assert len(value["proofs"]["works"]) == value["accepted_behavior_count"] == len(repo.views)
    assert value["handler_scenario_count"] == 2 * len(repo.views)
    assert value["costs"]["membership"]["state"] == "INVALID_MEMBERSHIP"
    assert value["costs"]["membership"]["reason_code"] == "CONFLICTING_MEMBERSHIP"
    assert value["costs"]["shared"] == amount([])
    expected_direct = repo.specs if fault == "exclude-bound" else repo.specs[:2]
    expected_unallocated = [] if fault == "exclude-bound" else repo.specs[2:]
    assert value["costs"]["direct"] == amount([(spec, 1) for spec in expected_direct])
    assert value["costs"]["unallocated"] == amount([(spec, 1) for spec in expected_unallocated])
    assert value["costs"]["totals"] == amount([(spec, 1) for spec in repo.specs])
    assert value["costs"]["allocation_state"] == "PARTIAL"
    assert value["token_per_accepted_behavior"] == {"codex": None, "provider": None}


def test_final_size_reserves_counter_growth_at_exact_measurement_boundary(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    actual_canonical = subject._canonical
    measured = []
    # Controlled MEASUREMENT seam only: the retained chain/returned whole report
    # remain real. This does not claim an actual2M retained report was generated.
    for extra in (0, 1):
        def measuring(value):
            if type(value) is dict and value.get("state") == "OBSERVATIONAL_ACCEPTED_WORK_COSTS":
                counter = value["snapshot"]["bytes_read"]
                growth = 7 - len(str(counter))
                size = 2000000 - growth + extra
                assert growth > 0 and 0 < size <= 2000000
                measured.append((extra, counter, growth, size))
                return " " * size
            return actual_canonical(value)
        with monkeypatch.context() as patcher:
            patcher.setattr(subject, "_canonical", measuring)
            if extra:
                with pytest.raises(subject.AcceptedWorkCostReportError) as caught:
                    subject.summarize_accepted_work(repo.root)
                assert caught.value.code == str(caught.value) == "BYTE_LIMIT"
            else:
                value, observed = collect(repo, monkeypatch)
                whole(repo, value, observed)
    assert [item[0] for item in measured] == [0, 1]
    assert all(size + growth == 2000000 + extra for extra, _, growth, size in measured)
    restored, observed = collect(repo, monkeypatch)
    whole(repo, restored, observed)


def test_proof_conflict_cannot_be_hidden_by_equal_normalized_cost_rows(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    repo.add_work(2)
    repo.add_work(3)
    alias = cost_record(repo.views[0], "proof-conflict-alias", state="BLOCKED")
    other = copy.deepcopy(alias)
    assert other["raw"]["task_sha256"] != "0" * 64
    other["raw"]["task_sha256"] = "0" * 64
    assert {key for key in alias["raw"] if alias["raw"][key] != other["raw"][key]} == {"task_sha256"}
    assert alias["normalized"] == other["normalized"]  # This field is NOT a cost dimension.
    repo.specs.extend((alias, other))
    row_sha = digest(canonical(alias["normalized"]))
    members = sorted(view.work for view in repo.views[1:])
    repo.declare_shared(entries=[{"cost_row_sha256": row_sha, "metrics_ordinals": [3, 4],
                                  "work_fingerprints": members}])
    value, _ = collect(repo, monkeypatch)
    assert [work["work_fingerprint"] for work in value["proofs"]["works"]] == members
    assert value["accepted_behavior_count"] == 2 and value["handler_scenario_count"] == 4
    assert value["proofs"]["run_bindings"] == [
        {"run_id": view.controller, "metrics_ordinals": [index],
         "semantic_fingerprint": view.identity["fingerprint"], "work_fingerprint": view.work,
         "state": "CONFLICT" if index == 0 else "BOUND"} for index, view in enumerate(repo.views)] + [
        {"run_id": "proof-conflict-alias", "metrics_ordinals": [3, 4],
         "semantic_fingerprint": None, "work_fingerprint": None, "state": "CONFLICT"}]
    assert value["proofs"]["retention_limits"]["proved_index_ordinals"] == [2, 3, 4, 5]
    assert value["proofs"]["retention_limits"]["unresolved_index_ordinals"] == [0, 1]
    costs = value["costs"]
    assert (costs["input_record_count"], costs["distinct_record_count"], costs["duplicate_record_count"]) == (5, 4, 1)
    assert costs["legacy_observation"]["run_conflict_count"] == 0
    assert costs["legacy_observation"]["logical_work_count"] == 3
    assert costs["rows"] == [{"metrics_ordinals": ordinals,
        "run_id": spec["normalized"]["run_id"], "cost_row_sha256": digest(canonical(spec["normalized"])),
        "window_state": "SELECTED", "allocation": "DIRECT" if index in (1, 2) else "UNALLOCATED",
        "work_fingerprints": [spec["owner"]] if index in (1, 2) else []}
        for index, (spec, ordinals) in enumerate(zip(repo.specs[:4], ([0], [1], [2], [3, 4])))]
    assert costs["membership"] == {"state": "INVALID_MEMBERSHIP", "reason_code": "CONFLICTING_MEMBERSHIP",
        "receipt_sha256": digest(repo.membership_body), "anchor_commit": repo.membership_anchor, "membership_count": 0}
    assert costs["shared"] == amount([])
    assert costs["direct"] == amount([(spec, 1) for spec in repo.specs[1:3]])
    assert costs["unallocated"] == amount([(repo.specs[0], 1), (alias, 1)])
    assert costs["totals"] == costs["retained_totals"] == amount([(spec, 1) for spec in repo.specs[:4]])
    assert costs["excluded"] == costs["unknown_time"] == amount([])
    assert costs["per_work"] == sorted([{"work_fingerprint": view.work, "costs": amount([(repo.specs[index], 1)])}
        for index, view in enumerate(repo.views) if index], key=lambda item: item["work_fingerprint"])
    assert value["denominator_state"] == "PARTIAL_RETAINED_WINDOW" and costs["allocation_state"] == "PARTIAL"
    assert value["token_per_accepted_behavior"] == {"codex": None, "provider": None}
