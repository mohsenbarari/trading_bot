"""Shared real-Git fixtures and independent whole oracles for BOTH 741 suites."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
from fractions import Fraction
import hashlib
import json
import math

import pytest

from scripts import two_site_accepted_work_cost_report as subject
from scripts import two_site_accepted_work_snapshot as custody
from tests.test_two_site_accepted_work_attestation import (
    ProofRepository, LEDGER, INVENTORY, GAP, METRICS, FIXED_LIMITS,
    canonical, raw, digest, blob_oid, read_counter,
)
from tests.test_two_site_accepted_work_proofs import expected_refs
from tests.test_two_site_work_cost_report import (
    costs as old_costs, report as old_report,
)

MEMBERSHIP = "accepted-work-shared-cost-membership-v1.json"
MEMBERSHIP_PATH = INVENTORY + MEMBERSHIP
CK = ("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens")
PK = ("input_tokens", "output_tokens", "total_tokens")
OBSERVED = "OBSERVED_CONTROLLER_ATTEMPTS"
PRIVATE = "private-741-fixture-not-report-text"
UTC_LITERALS = {"1970-01-01T00:01:39Z": 99, "1970-01-01T00:01:40Z": 100,
    "1970-01-01T00:01:40+00:00": 100, "1970-01-01T00:01:50Z": 110,
    "1970-01-01T00:01:40.5Z": Fraction(201, 2),
    "1970-01-01T00:01:40.000001+00:00": Fraction(100000001, 1000000),
    "1972-02-29T00:00:00Z": 68169600}


def rational(value=0):
    value = Fraction(value)
    return {"numerator": value.numerator, "denominator": value.denominator}


def _key(value):
    return canonical(value)


def known_time(value):
    if type(value) is str:
        return UTC_LITERALS.get(value)
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10 ** 12 else None


def cost_record(repo=None, run=None, *, codex=(100, 80, 20, 10), provider=(50, 20, 70),
                pair=(2, 2), seconds=10, finished=100, state=None, partial=False):
    """Declare RAW and independently expected canonical fields together, not via SUT."""
    record = copy.deepcopy(repo.metric) if repo else {"run_id": "shared-overhead", "state": "STOP"}
    if run is not None:
        record["run_id"] = run
    if state is not None:
        record["state"] = state
    record.update(model="gpt-6.1-sol", effort="xhigh", finished_at=finished)
    if seconds is not None:
        record["duration_seconds"] = seconds
    if codex is not None:
        record.update(usage_state="OBSERVED_COMPLETED_TURNS", **dict(zip(CK, codex)))
    pm = {"state": "PARTIAL" if partial else OBSERVED}
    nested = {}
    for key, short in (("semantic_identity", "semantic"), ("work_lineage", "work")):
        if key in record:
            pm[key] = copy.deepcopy(record[key])
            nested[short] = {"state": "VALID", "value": copy.deepcopy(record[key])}
        else:
            nested[short] = {"state": "ABSENT", "value": None}
    if provider is not None:
        pm.update(usage=dict(zip(PK, provider)), observed_usage_subtotal=dict(zip(PK, provider)))
    if pair is not None:
        pm.update(attempts=pair[0], usage_observed_attempts=pair[1])
    record["provider_metrics"] = pm
    normalized = {"schema_version": 1, "run_id": record["run_id"], "dimensions": {
        "model": "gpt-6.1-sol", "effort": "xhigh", "state": record["state"],
        "finished_at": float(finished) if type(finished) in (int, float) and known_time(finished) is not None else
                       finished if type(finished) is str and 1 <= len(finished) <= 80 and
                       all(32 <= ord(char) <= 126 for char in finished) else None,
        "head_before": record.get("head_before"), "head_after": record.get("head_after"),
        "worker_run_id": record.get("gemini_worker_run_id"),
        "reviewer_run_id": record.get("gemini_reviewer_run_id"),
        "proposal_sha256": record.get("proposal_sha256")},
        "semantic_identity": copy.deepcopy(record.get("semantic_identity")),
        "work_lineage": copy.deepcopy(record.get("work_lineage")),
        "provider_state": pm["state"], "nested_identities": nested,
        "group_eligible": repo is not None and not partial,
        "observed_candidate": repo is not None and not partial and record["state"] in ("CONTINUE", "COMPLETE"),
        "codex_usage": dict(zip(CK, codex)) if codex is not None else None,
        "provider_usage": {"observed_subtotal": dict(zip(PK, provider)) if provider is not None else None,
            "full_usage": dict(zip(PK, provider)) if provider is not None and pair is not None and
                          pair[0] == pair[1] and not partial else None,
            "attempt_pair": list(pair) if pair is not None else None},
        "reported_cycle_seconds": float(seconds) if seconds is not None else None}
    for container, key in ((normalized, "reported_cycle_seconds"), (normalized["dimensions"], "finished_at")):
        if type(container[key]) is float and container[key] == 0:
            container[key] = 0.0
    return {"raw": record, "normalized": normalized, "owner": repo.work if repo else None,
            "event": repo.controller if repo and record["run_id"] == repo.controller else None,
            "codex": codex, "provider": provider, "provider_full": normalized["provider_usage"]["full_usage"] is not None,
            "pair": pair, "seconds": seconds}


def malformed_record(value):
    return {"raw": value, "normalized": None, "owner": None, "event": None,
            "codex": None, "provider": None, "provider_full": False, "pair": None, "seconds": None}


def amount(specifications):
    """Independent arithmetic oracle from declared observation tuples and weights."""
    specifications = [(spec, Fraction(weight)) for spec, weight in specifications if weight]
    total = sum((weight for _, weight in specifications), Fraction())
    def observations(name, keys, full):
        observed = [(spec, weight) for spec, weight in specifications if spec[name] is not None]
        weight = sum((w for _, w in observed), Fraction())
        subtotal = {key: rational(sum((w * spec[name][i] for spec, w in observed), Fraction()))
                    for i, key in enumerate(keys)}
        return {"observed_weight": rational(weight), "unknown_weight": rational(total - weight),
                "observed_subtotal": subtotal,
                "full_usage": copy.deepcopy(subtotal) if total and all(full(spec) for spec, _ in specifications) else None}
    codex = observations("codex", CK, lambda spec: spec["codex"] is not None)
    provider = observations("provider", PK, lambda spec: spec["provider_full"])
    pairs = [(spec["pair"], weight) for spec, weight in specifications if spec["pair"] is not None]
    covered = sum((weight for _, weight in pairs), Fraction())
    reported = sum((pair[0] * weight for pair, weight in pairs), Fraction())
    observed = sum((pair[1] * weight for pair, weight in pairs), Fraction())
    provider.update(attempt_coverage_weight=rational(covered), unknown_attempt_coverage_weight=rational(total - covered),
        reported_attempt_count=rational(reported), observed_attempt_count=rational(observed),
        unknown_attempt_count=rational(reported - observed))
    times = [(spec["seconds"], weight) for spec, weight in specifications if spec["seconds"] is not None]
    timed = sum((weight for _, weight in times), Fraction())
    seconds = sum((Fraction(value) * weight for value, weight in times), Fraction())
    return {"row_weight": rational(total), "codex": codex, "provider": provider,
        "reported_cycle_seconds": {"observed_weight": rational(timed), "unknown_weight": rational(total - timed),
            "observed_subtotal": rational(seconds), "full_total": rational(seconds) if total and timed == total else None}}


def legacy_cost(specs):
    c = amount([(spec, 1) for spec in specs])
    n = lambda value: Fraction(value["numerator"], value["denominator"])
    codex, provider, timing = c["codex"], c["provider"], c["reported_cycle_seconds"]
    return old_costs((int(n(codex["observed_weight"])), int(n(codex["unknown_weight"])),
        tuple(int(n(codex["observed_subtotal"][key])) for key in CK), codex["full_usage"] is not None),
        (int(n(provider["observed_weight"])), int(n(provider["unknown_weight"])),
         tuple(int(n(provider["observed_subtotal"][key])) for key in PK), provider["full_usage"] is not None,
         int(n(provider["attempt_coverage_weight"])), int(n(provider["unknown_attempt_coverage_weight"])),
         int(n(provider["reported_attempt_count"])), int(n(provider["observed_attempt_count"]))),
        (int(n(timing["observed_weight"])), int(n(timing["unknown_weight"])),
         math.fsum(sorted(float(spec["seconds"]) for spec in specs if spec["seconds"] is not None)),
         timing["full_total"] is not None))


def groups(specs):
    """Fixture group declarations: only exact canonical known-run duplicates collapse."""
    result = []
    for ordinal, spec in enumerate(specs):
        normalized = spec["normalized"]
        match = next((group for group in result if normalized is not None and
            normalized["run_id"] is not None and group["spec"]["normalized"] == normalized), None)
        if match is None:
            result.append({"spec": spec, "ordinals": [ordinal]})
        else:
            match["ordinals"].append(ordinal)
    return result


class ReportRepository(ProofRepository):
    def __init__(self, root, form="inline", linked=False):
        super().__init__(root, form=form, linked=linked)
        self.views = [copy.deepcopy(self)]
        self.specs = [cost_record(self)]
        self.membership_body = None
        self.membership_anchor = None
        self.save_metrics()

    def save_metrics(self):
        self.artifact(METRICS, [spec["raw"] for spec in self.specs])
        for view in self.views:
            view.artifacts[METRICS] = self.artifacts[METRICS]
            view.head, view.ledger = self.head, self.ledger

    def add_work(self, number):
        """A new documented scenario, real new test delta and sole-parent accepted commit."""
        previous = [(view.receipt_path, raw(view.receipt)) for view in self.views]
        old_rows = copy.deepcopy(self.index["rows"])
        other = copy.deepcopy(self.views[-1])
        other.commit_bodies = self.commit_bodies
        other.parent = other.commit("new scenario generation parent", [])
        other.accepted_path = f"tests/test_retained_scenario_{number}.py"
        other.after = (f"class TestLiteral:\n    def test_buy(self):\n        assert {number} * 7 == {number * 7}\n"
                       f"\n    def test_sell(self):\n        assert {number} + 11 == {number + 11}\n").encode()
        other.put(other.accepted_path, other.after)
        other.accepted = other.commit("new scenario accepted Source", [other.accepted_path])
        manifest = {path: digest(body) for path, body in other.source.items()}
        manifest[other.accepted_path] = None
        other.identity.update(scenario_key=f"independent-literal-{number}", source_manifest=manifest,
                              source_sha256=digest(canonical(manifest)))
        other.identity["fingerprint"] = digest(canonical({key: other.identity[key] for key in
            ("stage_id", "gap_reference", "scenario_key", "symbol_groups", "source_sha256")}))
        other.work = digest(canonical({"kind": "two-site-logical-work", "schema_version": 1,
            **{key: other.identity[key] for key in ("stage_id", "gap_reference", "scenario_key", "symbol_groups")}}))
        other.task_id, other.controller, other.prefix = f"scenario-task-{number}", f"scenario-controller-{number}", f"scenario-{number}"
        other.worker, other.reviewer = other.prefix + "-worker-0", other.prefix + "-reviewer-0"
        other.selectors = [other.accepted_path + "::TestLiteral::test_" + name for name in ("buy", "sell")]
        bundle = {"head": other.parent, "gap_reference": GAP, "excerpts": other.excerpts, "semantic_identity": other.identity}
        other.task.update(task_id=other.task_id, goal="Independent numeric scenario.\nCANONICAL_SOURCE_EXCERPTS_JSON:\n" +
                          canonical(bundle).decode(), allowed_files=[other.accepted_path], test_selectors=other.selectors)
        other.sources = {path: {"content": other.source[path].decode(), "sha256": digest(other.source[path])}
                         for path in other.context}
        other.sources[other.accepted_path] = {"content": None, "sha256": None}
        other.worker_snapshot = {"task": other.task, "source_head": other.parent, "sources": other.sources}
        other.proposal["changes"] = [{"path": other.accepted_path, "original_sha256": None, "content": other.after.decode()}]
        other.proposal_bytes = raw(other.proposal)
        other.native.update(proposal_sha256=digest(other.proposal_bytes), selectors=other.selectors)
        for status, run in ((other.worker_status, other.worker), (other.reviewer_status, other.reviewer)):
            status.update(run_id=run, task_id=other.task_id, head=other.parent, controller_run_id=other.controller)
        other.worker_status["result_sha256"] = digest(other.proposal_bytes)
        other.reviewer_status.update(input_worker_run_id=other.worker, input_proposal_sha256=digest(other.proposal_bytes),
                                     input_test_evidence_sha256=digest(raw(other.native)))
        other.reviewer_snapshot = {**copy.deepcopy(other.worker_snapshot), "proposal": other.proposal_bytes.decode(),
                                   "test_evidence": copy.deepcopy(other.native)}
        other.root_result.update(commit=other.accepted, gemini_worker_run_id=other.worker,
            gemini_reviewer_run_id=other.reviewer, proposal_sha256=digest(other.proposal_bytes))
        other.root_status.update(head_before=other.parent, head_after=other.accepted,
            gemini_worker_run_id=other.worker, gemini_reviewer_run_id=other.reviewer,
            proposal_sha256=digest(other.proposal_bytes))
        other.write_retained()
        def scenarios(receipt):
            receipt["scope"]["scenarios_covered"] = [f"{name}-literal-{number}" for name in ("buy", "sell")]
            for handler in receipt["registered_handlers"]:
                handler["scenario_key"] += f"-{number}"
                handler["seed_id"] += f"-{number}"
        other.publish(scenarios)
        other.receipt_path = INVENTORY + f"fixture-independent-{number}.json"
        other.put(other.receipt_path, raw(other.receipt))
        for path, body in previous:
            other.put(path, body)
        other.commit("retain distinct scenario receipts", [other.receipt_path, *[path for path, _ in previous]])
        for row in other.rows:
            row[6] = row[9] = other.receipt_path
        other.index["rows"] = old_rows + other.rows
        other.write_index()
        self.root, self.head, self.ledger, self.index = other.root, other.head, other.ledger, other.index
        self.commit_bodies.update(other.commit_bodies)
        self.artifacts.update(other.artifacts)
        self.views.append(other)
        self.specs.append(cost_record(other, finished=100 + number))
        self.save_metrics()
        return other

    def declare_shared(self, entries=None, transform=None):
        self.save_metrics()
        self.membership_anchor = self.head
        if entries is None:
            entry = groups(self.specs)[-1]
            entries = [{"cost_row_sha256": digest(canonical(entry["spec"]["normalized"])),
                "metrics_ordinals": entry["ordinals"], "work_fingerprints": sorted(view.work for view in self.views)}]
        declaration = {"schema_version": "two-site.accepted-work-shared-cost-membership.v1",
            "anchor_commit": self.membership_anchor, "canonical_ledger_sha256": digest(self.ledger),
            "metrics_sha256": digest(self.artifacts[METRICS]), "memberships": entries}
        if transform:
            transform(declaration)
        self.membership_body = raw(declaration)
        self.put(MEMBERSHIP_PATH, self.membership_body)
        self.head = self.commit("committed fixed shared-membership declaration", [MEMBERSHIP_PATH])
        self.save_metrics()
        return declaration


def expected_legacy(specs):
    distinct = [group["spec"] for group in groups(specs)]
    lineages, grouped = [], set()
    for work in sorted({spec["owner"] for spec in distinct if spec["owner"]}):
        rows = [spec for spec in distinct if spec["owner"] == work and spec["normalized"]["group_eligible"]]
        if not rows:
            continue
        grouped.update(id(spec) for spec in rows)
        lineages.append({"work_fingerprint": work,
            "semantic_attempt_count": len({spec["normalized"]["semantic_identity"]["fingerprint"] for spec in rows}),
            "source_revision_count": len({spec["normalized"]["semantic_identity"]["source_sha256"] for spec in rows}),
            "record_count": len(rows), "observed_candidate_count": sum(spec["normalized"]["observed_candidate"] for spec in rows),
            "costs": legacy_cost(rows)})
    unallocated = [spec for spec in distinct if id(spec) not in grouped]
    malformed = sum(spec["normalized"] is None for spec in unallocated)
    return old_report(len(specs), len(distinct), legacy_cost(distinct), lineages,
        attempts=sum(line["semantic_attempt_count"] for line in lineages),
        candidates=sum(line["observed_candidate_count"] > 0 for line in lineages),
        duplicates=len(specs) - len(distinct), unattributed=(len(unallocated), 0, malformed),
        unknown_cost=legacy_cost(unallocated))


def proof_oracle(repo):
    records = repo.specs
    metric_ref = {"path": METRICS, "sha256": digest(repo.artifacts[METRICS]), "pointer": ""}
    works, scenarios, bindings, unknown = [], [], [], []
    for view in repo.views:
        refs = expected_refs(view)
        runs = sorted(spec["raw"]["run_id"] for spec in records if spec["owner"] == view.work)
        works.append({"work_fingerprint": view.work, "semantic_fingerprints": [view.identity["fingerprint"]],
            "source_revisions": [{"semantic_fingerprint": view.identity["fingerprint"],
                "source_sha256": view.identity["source_sha256"], "source_head": view.parent,
                "accepted_source_commit": view.accepted}],
            "evidence_level": "ORIGINAL_ROOT_RECEIPT_CORROBORATED" if view.root_receipts else "HISTORICAL_ATTESTED_CORROBORATED",
            "proof_refs": refs, "retained_run_ids": sorted(set(runs))})
        for index, handler in enumerate(view.handlers):
            scenarios.append({"work_fingerprint": view.work, "gap_reference": GAP,
                "canonical_symbol": handler["canonical_symbol"], "scenario_key": handler["scenario_key"],
                "witnesses": [{"selector": view.selectors[index], "source_commit": view.accepted,
                               "source_file_sha256": digest(view.after)}]})
    for group in groups(records):
        spec, ordinals = group["spec"], group["ordinals"]
        normalized = spec["normalized"]
        run = normalized["run_id"] if normalized else None
        owner = spec["owner"]
        semantic = spec["raw"]["semantic_identity"]["fingerprint"] if owner else None
        bindings.append({"run_id": run, "metrics_ordinals": ordinals,
            "semantic_fingerprint": semantic, "work_fingerprint": owner, "state": "BOUND" if owner else "UNBOUND"})
        if not owner:
            unknown.append({"work_fingerprint": None, "semantic_fingerprint": None,
                "reason_code": "UNBOUND_METRICS", "proof_refs": [metric_ref],
                "retained_run_ids": [run] if run else [], "metrics_ordinals": ordinals, "index_ordinals": []})
    limits = set(FIXED_LIMITS)
    for view in repo.views:
        if view.form != "inline":
            limits.add("ORIGINAL_ROOT_NOT_DECLARED")
        elif not view.root_receipts:
            limits.add("MISSING_ORIGINAL_ROOT_RECEIPTS")
    if unknown:
        limits.add("UNKNOWN_RETAINED_ROWS")
    merged, coverage = {}, {}
    for work in works:
        previous = merged.setdefault(work["work_fingerprint"], copy.deepcopy(work))
        for key in ("semantic_fingerprints", "source_revisions", "proof_refs", "retained_run_ids"):
            values = {_key(value): value for value in previous[key] + work[key]}
            previous[key] = sorted(values.values(), key=_key)
        previous["proof_refs"].sort(key=lambda ref: (ref["path"], ref["sha256"], ref["pointer"]))
    for scenario in scenarios:
        key = scenario["gap_reference"], scenario["canonical_symbol"], scenario["scenario_key"]
        previous = coverage.setdefault(key, copy.deepcopy(scenario))
        previous["witnesses"] = list({_key(item): item for item in previous["witnesses"] + scenario["witnesses"]}.values())
        previous["witnesses"].sort(key=canonical)
    return {"works": [merged[key] for key in sorted(merged)],
        "handler_scenarios": [coverage[key] for key in sorted(coverage)],
        "run_bindings": bindings, "unknown": sorted(unknown, key=canonical),
        "retention_limits": {"index_row_count": len(repo.index["rows"]), "metrics_record_count": len(records),
            "proved_index_ordinals": list(range(len(repo.index["rows"]))), "unresolved_index_ordinals": [], "limitations": sorted(limits)}}


def snapshot_oracle(repo, observed):
    blobs, objects, commits = {}, {}, {repo.head}
    files = dict(repo.artifacts)
    files[LEDGER] = repo.ledger
    for view in repo.views:
        objects.update({blob_oid(body): body for body in view.source.values()})
        objects[blob_oid(view.after)] = view.after
        blobs.update(view.source)
        blobs.update({view.accepted_path: view.after, view.receipt_path: raw(view.receipt), LEDGER: repo.ledger})
        files[view.receipt_path] = raw(view.receipt)
        commits.update((view.parent, view.accepted))
        if view.registration is not None:
            blobs[view.registration_path] = raw(view.registration)
            commits.add(view.registration_commit)
        if view.custody_doc is not None:
            blobs[view.custody_path] = raw(view.custody_doc)
            commits.add(view.custody_commit)
    if repo.membership_body is not None:
        files[MEMBERSHIP_PATH] = repo.membership_body
        blobs[MEMBERSHIP_PATH] = repo.membership_body
        commits.add(repo.membership_anchor)
    objects.update({blob_oid(body): body for body in blobs.values()})
    objects.update({commit: repo.commit_bodies[commit] for commit in commits})
    # Public accounting includes normal exit's six fixed identity queries. The
    # private740 ATCALL API remains unchanged; verify all final bytes explicitly.
    final_identity = (str(repo.root) + "\nsha1\n" + repo.head + "\n" +
                      "".join(value + "\n" for value in repo.refs.values())).encode()
    wire = b"".join(item[2] for item in observed)
    assert wire.endswith(final_identity)
    return {"schema_version": 1, "head": repo.head, "protected_refs": repo.refs,
        "files": [{"path": path, "sha256": digest(body), "size": len(body)} for path, body in sorted(files.items())],
        "git_objects": [{"git_oid": oid, "sha256": digest(body), "size": len(body)} for oid, body in sorted(objects.items())],
        "bytes_read": len(wire)}


def report_oracle(repo, observed, window=None):
    proof = proof_oracle(repo)
    selected, excluded, unknown_time, event_works, events = [], [], [], set(), []
    for group in groups(repo.specs):
        value = group["spec"]["normalized"]
        finished = value["dimensions"]["finished_at"] if value else None
        stamp = known_time(finished)
        target = selected if window is None or stamp is not None and window["start_utc"] <= stamp < window["end_utc"] else excluded if stamp is not None else unknown_time
        target.append(group)
    for view in repo.views:
        original = [i for i, spec in enumerate(repo.specs) if spec["event"] == view.controller]
        timestamps = [known_time(repo.specs[i]["raw"].get("finished_at")) for i in original]
        known = bool(timestamps) and timestamps[0] is not None and all(value == timestamps[0] for value in timestamps)
        timestamp = timestamps[0] if known else None
        events.append({"work_fingerprint": view.work, "semantic_fingerprint": view.identity["fingerprint"],
            "accepted_source_commit": view.accepted, "controller_run_id": view.controller,
            "metrics_ordinals": original, "accepted_at_utc": rational(timestamp) if known else None,
            "evidence_level": "ORIGINAL_ROOT_RECEIPT_CORROBORATED" if view.root_receipts else "HISTORICAL_ATTESTED_CORROBORATED"})
    first_events = list(events) if window is None else []
    if window is not None:
        for work in {event["work_fingerprint"] for event in events}:
            candidates = [event for event in events if event["work_fingerprint"] == work]
            if any(event["accepted_at_utc"] is None for event in candidates):
                continue
            earliest = min(Fraction(event["accepted_at_utc"]["numerator"], event["accepted_at_utc"]["denominator"])
                           for event in candidates)
            if window["start_utc"] <= earliest < window["end_utc"]:
                first_events.extend(event for event in candidates if event["accepted_at_utc"] == rational(earliest))
    event_works = {event["work_fingerprint"] for event in first_events}
    first_commits = {(event["work_fingerprint"], event["accepted_source_commit"]) for event in first_events}
    handler_count = sum(any((scenario["work_fingerprint"], witness["source_commit"]) in first_commits
        for witness in scenario["witnesses"]) for scenario in proof["handler_scenarios"])
    selected_ordinals = sorted(i for group in selected for i in group["ordinals"])
    unknown_ordinals = sorted(i for group in unknown_time for i in group["ordinals"])
    membership = {"state": "NO_RETAINED_MEMBERSHIP", "reason_code": None, "receipt_sha256": None,
                  "anchor_commit": None, "membership_count": 0}
    declared = {}
    if repo.membership_body is not None:
        document = json.loads(repo.membership_body)
        membership.update(state="VERIFIED", receipt_sha256=digest(repo.membership_body),
                          anchor_commit=repo.membership_anchor, membership_count=len(document["memberships"]))
        declared = {tuple(entry["metrics_ordinals"]): entry["work_fingerprints"] for entry in document["memberships"]}
    direct, shared, unallocated, per_work, rows = [], [], [], {work: [] for work in event_works}, []
    outside_members = False
    for group in groups(repo.specs):
        spec, ordinals = group["spec"], group["ordinals"]
        normalized = spec["normalized"]
        window_state = "SELECTED" if ordinals[0] in selected_ordinals else "UNKNOWN_TIME" if ordinals[0] in unknown_ordinals else "EXCLUDED"
        owners, allocation = [], "UNALLOCATED"
        if window_state == "SELECTED":
            owners = [spec["owner"]] if spec["owner"] in event_works else []
            if tuple(ordinals) in declared:
                members = declared[tuple(ordinals)]
                if set(members) <= event_works:
                    owners, allocation = members, "SHARED"
                    shared.append((spec, 1))
                else:
                    owners, outside_members = [], True
                    unallocated.append((spec, 1))
            elif owners:
                allocation = "DIRECT"
                direct.append((spec, 1))
            else:
                unallocated.append((spec, 1))
            for owner in owners:
                per_work.setdefault(owner, []).append((spec, Fraction(1, len(owners))))
        rows.append({"metrics_ordinals": ordinals, "run_id": normalized["run_id"] if normalized else None,
            "cost_row_sha256": digest(canonical(normalized)) if normalized else None,
            "allocation": allocation, "work_fingerprints": sorted(owners), "window_state": window_state})
    weighted = lambda bucket: amount([(group["spec"], 1) for group in bucket])
    total = weighted(selected)
    event_unknown = window is not None and any(event["accepted_at_utc"] is None for event in events)
    complete = not proof["unknown"] and not unknown_time and not event_unknown
    denominator = "COMPLETE_RETAINED_WINDOW" if complete else "PARTIAL_RETAINED_WINDOW"
    allocation_state = "COMPLETE" if selected and not unallocated else "PARTIAL"
    ratio_ok = bool(event_works) and complete and allocation_state == "COMPLETE" and not unknown_time
    ratios = {"codex": None, "provider": None}
    for name in ratios:
        if ratio_ok and total[name]["full_usage"] is not None:
            ratios[name] = {key: rational(Fraction(value["numerator"], value["denominator"]) / len(event_works))
                            for key, value in total[name]["full_usage"].items()}
    limits = set(proof["retention_limits"]["limitations"]) - {"PRIVATE_PROOF_PHASE_ONLY", "PUBLIC_COST_REPORT_PENDING"}
    limits.add("HISTORICAL_TIMING_UNVERIFIED")
    if repo.membership_body is None:
        limits.add("NO_RETAINED_MEMBERSHIP")
    if unknown_time or event_unknown:
        limits.add("UNKNOWN_WINDOW_TIMESTAMPS")
    if outside_members:
        limits.add("WINDOW_MEMBERSHIP_OUTSIDE_DENOMINATOR")
    return {"schema_version": 1, "state": "OBSERVATIONAL_ACCEPTED_WORK_COSTS",
        "snapshot": snapshot_oracle(repo, observed), "window": {
            "kind": "RETAINED_FULL" if window is None else "UTC_HALF_OPEN",
            "acceptance_rule": "FIRST_RETAINED_CORROBORATED_ACCEPTANCE",
            "start_utc": window["start_utc"] if window else None, "end_utc": window["end_utc"] if window else None,
            "selected_ordinals": selected_ordinals, "unknown_time_ordinals": unknown_ordinals}, "proofs": proof,
        "accepted_behavior_count": len(event_works), "handler_scenario_count": handler_count,
        "denominator_state": denominator, "acceptance_events": sorted(events,
            key=lambda event: (event["work_fingerprint"], event["accepted_source_commit"], event["controller_run_id"])),
        "costs": {"input_record_count": len(repo.specs), "distinct_record_count": len(rows),
            "duplicate_record_count": len(repo.specs) - len(rows), "rows": rows, "totals": total,
            "retained_totals": weighted(groups(repo.specs)), "excluded": weighted(excluded), "unknown_time": weighted(unknown_time),
            "direct": amount(direct), "shared": amount(shared), "unallocated": amount(unallocated),
            "per_work": [{"work_fingerprint": work, "costs": amount(specs)} for work, specs in sorted(per_work.items())],
            "membership": membership, "allocation_state": allocation_state, "legacy_observation": expected_legacy(repo.specs)},
        "timing": {"reported_cycle_seconds": total["reported_cycle_seconds"], "active_seconds": None,
                   "quota_wait_seconds": None, "calendar_seconds": None, "state": "HISTORICAL_TIMING_UNVERIFIED"},
        "token_per_accepted_behavior": ratios, "billing_savings": None, "value_state": "NOT_ESTABLISHED",
        "cohort_comparability": "NOT_ESTABLISHED", "limitations": sorted(limits)}


def collect(repo, monkeypatch, window=None):
    with read_counter(monkeypatch) as observed:
        value = subject.summarize_accepted_work(repo.root, window=window)
    return value, observed


def whole(repo, value, observed, window=None):
    assert value == report_oracle(repo, observed, window)
    assert len(value) == 16 and json.loads(json.dumps(value, allow_nan=False)) == value
    assert PRIVATE not in json.dumps(value)
