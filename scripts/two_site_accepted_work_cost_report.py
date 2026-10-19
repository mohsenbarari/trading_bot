"""CODEX retained accepted-work observations; no billing or execution authority."""
from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timezone
from fractions import Fraction

from scripts.two_site_accepted_work_attestation import _canonical, _sha, _json, _ProofIssue
from scripts.two_site_accepted_work_evidence import _EvidenceFilesError, _copy_fixed_notes
from scripts.two_site_accepted_work_proofs import _Collector, _AcceptedWorkProofError, _FATAL
from scripts.two_site_accepted_work_snapshot import _proof_snapshot, _EvidenceSnapshotError
from scripts.two_site_work_cost_rows import normalize_work_cost_row
from scripts.two_site_work_cost_report import summarize_work_lineage_ledger

__all__ = ["AcceptedWorkCostReportError", "summarize_accepted_work"]

_ERRORS = _FATAL | {"INVALID_WINDOW"}
_CODEX = ("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens")
_PROVIDER = ("input_tokens", "output_tokens", "total_tokens")
_MEMBERSHIP = "accepted-work-shared-cost-membership-v1.json"
_RECEIPT = "docs/refactor/two-server/section-1/inventory/" + _MEMBERSHIP
_VERSION = "two-site.accepted-work-shared-cost-membership.v1"
_UTC = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
                  r"(?:\.([0-9]{1,6}))?(?:Z|\+00:00)", re.ASCII)
_ALWAYS = {"RETAINED_LEDGER_ONLY", "NOT_FULL_HISTORY", "LIFETIME_UNVERIFIED",
           "HISTORICAL_TIMING_UNVERIFIED", "NO_BILLING_VALUE_STAGE_AUTHORITY"}


class AcceptedWorkCostReportError(ValueError):
    """Fixed source-free error from the bounded report boundary."""

    def __init__(self, code):
        self.code = code if type(code) is str and code in _ERRORS else "GIT_FAILURE"
        super().__init__(self.code)


def _rational(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def _number(value):
    if type(value) is int and 0 <= value <= 1000000000000:
        return Fraction(value)
    if type(value) is float and math.isfinite(value) and 0 <= value <= 1000000000000:
        return Fraction(value)
    return None


def _timestamp(value):
    numeric = _number(value)
    if numeric is not None:
        return numeric
    if type(value) is not str or len(value) > 32:
        return None
    match = _UTC.fullmatch(value)
    if match is None:
        return None
    try:
        parts = [int(part) for part in match.groups()[:6]]
        microseconds = int((match.group(7) or "").ljust(6, "0"))
        stamp = datetime(*parts, microsecond=microseconds, tzinfo=timezone.utc)
        delta = stamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
        result = Fraction(delta.days * 86400 + delta.seconds) + Fraction(delta.microseconds, 1000000)
        return result if 0 <= result <= 1000000000000 else None
    except (ValueError, OverflowError):
        return None


def _window(value):
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {"start_utc", "end_utc"} or
            any(type(value[key]) is not int for key in value) or
            not 0 <= value["start_utc"] < value["end_utc"] <= 1000000000000):
        raise AcceptedWorkCostReportError("INVALID_WINDOW") from None
    return dict(value)


def _normalize(value):
    row = normalize_work_cost_row(value)
    if row is not None:
        if type(row["reported_cycle_seconds"]) is float and row["reported_cycle_seconds"] == 0:
            row["reported_cycle_seconds"] = 0.0
        dimensions = row["dimensions"]
        if type(dimensions["finished_at"]) is float and dimensions["finished_at"] == 0:
            dimensions["finished_at"] = 0.0
    return row


class _Costs:
    """Exact additive observed masses, with independent complete-coverage gates."""

    def __init__(self):
        self.weight = Fraction(0)
        self.codex = dict.fromkeys(_CODEX, Fraction(0))
        self.provider = dict.fromkeys(_PROVIDER, Fraction(0))
        self.co = self.cu = self.po = self.pu = self.pf = Fraction(0)
        self.ao = self.au = self.reported = self.observed = Fraction(0)
        self.to = self.tu = self.seconds = Fraction(0)

    def add(self, row, weight=Fraction(1)):
        self.weight += weight
        codex = row["codex_usage"] if row is not None else None
        if codex is None:
            self.cu += weight
        else:
            self.co += weight
            for key in _CODEX:
                self.codex[key] += weight * codex[key]
        provider = row["provider_usage"] if row is not None else {}
        subtotal = provider.get("observed_subtotal")
        if subtotal is None:
            self.pu += weight
        else:
            self.po += weight
            for key in _PROVIDER:
                self.provider[key] += weight * subtotal[key]
        if provider.get("full_usage") is not None:
            self.pf += weight
        attempts = provider.get("attempt_pair")
        if attempts is None:
            self.au += weight
        else:
            self.ao += weight
            self.reported += weight * attempts[0]
            self.observed += weight * attempts[1]
        seconds = _number(row["reported_cycle_seconds"]) if row is not None else None
        if seconds is None:
            self.tu += weight
        else:
            self.to += weight
            self.seconds += weight * seconds

    def value(self):
        codex = {key: _rational(value) for key, value in self.codex.items()}
        provider = {key: _rational(value) for key, value in self.provider.items()}
        return {"row_weight": _rational(self.weight),
            "codex": {"observed_weight": _rational(self.co), "unknown_weight": _rational(self.cu),
                "observed_subtotal": codex, "full_usage": copy.deepcopy(codex) if self.weight and not self.cu else None},
            "provider": {"observed_weight": _rational(self.po), "unknown_weight": _rational(self.pu),
                "observed_subtotal": provider,
                "full_usage": copy.deepcopy(provider) if self.weight and self.pf == self.weight else None,
                "attempt_coverage_weight": _rational(self.ao), "unknown_attempt_coverage_weight": _rational(self.au),
                "reported_attempt_count": _rational(self.reported), "observed_attempt_count": _rational(self.observed),
                "unknown_attempt_count": _rational(self.reported - self.observed)},
            "reported_cycle_seconds": {"observed_weight": _rational(self.to), "unknown_weight": _rational(self.tu),
                "observed_subtotal": _rational(self.seconds),
                "full_total": _rational(self.seconds) if self.weight and not self.tu else None}}


class _Proofs(_Collector):
    """Keep causal original-controller associations, without changing740 output."""

    def aggregate(self, rows, proven):
        result = super().aggregate(rows, proven)
        survivors = {work["work_fingerprint"] for work in result["works"]}
        events = {}
        for proof in proven:
            self.context.tick()
            if proof["work"] not in survivors:
                continue
            key = proof["work"], proof["commit"], proof["controller"], proof["identity"]["fingerprint"]
            stamps = [_timestamp(self.records[index].get("finished_at")) for index in proof["ordinals"]]
            stamp = stamps[0] if stamps and stamps[0] is not None and all(item == stamps[0] for item in stamps) else None
            event = {"work_fingerprint": proof["work"], "semantic_fingerprint": key[3],
                "accepted_source_commit": proof["commit"], "controller_run_id": proof["controller"],
                "metrics_ordinals": sorted(set(proof["ordinals"])), "accepted_at_utc": stamp,
                "evidence_level": proof["tier"]}
            if key in events:
                event["metrics_ordinals"] = sorted(set(event["metrics_ordinals"] + events[key]["metrics_ordinals"]))
                if events[key]["evidence_level"] == "ORIGINAL_ROOT_RECEIPT_CORROBORATED":
                    event["evidence_level"] = events[key]["evidence_level"]
            events[key] = event
        self.events = [events[key] for key in sorted(events)]
        return result


class _MembershipIssue(Exception):
    pass


def _require(value, reason):
    if not value:
        raise _MembershipIssue(reason) from None


def _hex(value, length):
    return type(value) is str and len(value) == length and all(char in "0123456789abcdef" for char in value)


class _Report:
    def __init__(self, snapshot, window):
        self.snapshot, self.window = snapshot, window
        self.owner = snapshot._files
        self.limits = set(_ALWAYS)

    def tick(self):
        self.owner._check_time()

    def _groups(self, records):
        groups, runs = [], {}
        for ordinal, raw in enumerate(records):
            self.tick()
            row = _normalize(raw)
            run = row["run_id"] if row is not None else None
            previous = next((item for item in runs.get(run, ()) if item["row"] == row), None) if run else None
            if previous is not None:
                previous["ordinals"].append(ordinal)
                continue
            group = {"row": row, "ordinals": [ordinal], "run": run,
                     "sha": _sha(_canonical(row).encode("utf-8")) if row is not None else None}
            groups.append(group)
            if run:
                runs.setdefault(run, []).append(group)
        self.run_conflicts = {run for run, variants in runs.items() if len(variants) > 1}
        return groups

    def _membership(self, groups, proofs, events):
        info = {"state": "NO_RETAINED_MEMBERSHIP", "reason_code": None,
                "receipt_sha256": None, "anchor_commit": None, "membership_count": 0}
        body = None
        try:
            body = self.snapshot._read("receipt", _MEMBERSHIP)
        except _EvidenceSnapshotError as error:
            if error.code != "MISSING_FILE":
                raise
        if body is not None:
            info["receipt_sha256"] = _sha(body)
        try:
            blob = self.snapshot._git_blob(self.snapshot._head, _RECEIPT)
            if body is None and blob is None:
                self.limits.add("NO_RETAINED_MEMBERSHIP")
                return info, {}
            _require(body is not None and blob is not None and blob["data"] == body, "HASH_MISMATCH")
            try:
                declaration = _json(body, self.tick)
            except _ProofIssue:
                raise _MembershipIssue("MALFORMED_MEMBERSHIP") from None
            _require(type(declaration) is dict and set(declaration) == {"schema_version", "anchor_commit",
                "canonical_ledger_sha256", "metrics_sha256", "memberships"}, "MALFORMED_MEMBERSHIP")
            _require(declaration["schema_version"] == _VERSION, "UNSUPPORTED_SCHEMA")
            anchor = declaration["anchor_commit"]
            _require(_hex(anchor, 40) and anchor != self.snapshot._head, "ANCHOR_MISMATCH")
            _require(self.snapshot._is_ancestor(anchor, self.snapshot._head), "ANCHOR_MISMATCH")
            info["anchor_commit"] = anchor
            _require(declaration["canonical_ledger_sha256"] == _sha(self.snapshot._read("ledger")) and
                     declaration["metrics_sha256"] == _sha(self.snapshot._read("metrics")), "HASH_MISMATCH")
            entries = declaration["memberships"]
            _require(type(entries) is list and len(entries) <= 2000, "MALFORMED_MEMBERSHIP")
            known = {work["work_fingerprint"] for work in proofs["works"]}
            rows = {tuple(group["ordinals"]): group for group in groups}
            bindings = {ordinal: binding for binding in proofs["run_bindings"] for ordinal in binding["metrics_ordinals"]}
            allocation = {}
            for entry in entries:
                self.tick()
                _require(type(entry) is dict and set(entry) == {"cost_row_sha256", "metrics_ordinals", "work_fingerprints"},
                         "MALFORMED_MEMBERSHIP")
                digest, ordinals, members = entry["cost_row_sha256"], entry["metrics_ordinals"], entry["work_fingerprints"]
                _require(_hex(digest, 64) and type(ordinals) is list and 1 <= len(ordinals) <= 2000 and
                    all(type(index) is int and index >= 0 for index in ordinals) and ordinals == sorted(set(ordinals)) and
                    type(members) is list and 2 <= len(members) <= 512 and all(_hex(member, 64) for member in members) and
                    members == sorted(set(members)), "MALFORMED_MEMBERSHIP")
                self.owner._charge_metadata(("shared-membership", digest, tuple(ordinals)),
                                            len(_canonical(entry).encode("utf-8")))
                key = tuple(ordinals)
                _require(key not in allocation, "CONFLICTING_MEMBERSHIP")
                group = rows.get(key)
                _require(group is not None and group["row"] is not None and group["sha"] == digest, "ROW_MISMATCH")
                _require(group["run"] not in self.run_conflicts and
                    all(bindings[index]["state"] != "CONFLICT" for index in ordinals), "CONFLICTING_MEMBERSHIP")
                bound = {bindings[index]["work_fingerprint"] for index in ordinals if bindings[index]["state"] == "BOUND"}
                _require(bound <= set(members), "CONFLICTING_MEMBERSHIP")
                _require(set(members) <= known, "MEMBER_MISMATCH")
                for member in members:
                    accepted = [event["accepted_source_commit"] for event in events if event["work_fingerprint"] == member]
                    _require(any(self.snapshot._is_ancestor(commit, anchor) for commit in accepted), "MEMBER_MISMATCH")
                allocation[key] = list(members)
            info.update(state="VERIFIED", membership_count=len(allocation))
            return info, allocation
        except _MembershipIssue as error:
            reason = str(error)
            info.update(state="UNSUPPORTED_MEMBERSHIP" if reason == "UNSUPPORTED_SCHEMA" else "INVALID_MEMBERSHIP",
                        reason_code=reason, membership_count=0)
            self.limits.add("SHARED_MEMBERSHIP_UNVERIFIED")
            return info, {}

    def _selection(self, stamp):
        if self.window is None:
            return "SELECTED"
        if stamp is None:
            return "UNKNOWN_TIME"
        return "SELECTED" if self.window["start_utc"] <= stamp < self.window["end_utc"] else "EXCLUDED"

    def run(self):
        try:
            return self._run()
        except (MemoryError, RecursionError):
            raise _EvidenceFilesError("COUNT_LIMIT") from None

    def _run(self):
        collector = _Proofs(self.snapshot)
        proofs = collector.run()
        records, events = collector.records, collector.events
        groups = self._groups(records)
        membership, allocations = self._membership(groups, proofs, events)
        self.limits.update(set(proofs["retention_limits"]["limitations"]) -
                           {"PRIVATE_PROOF_PHASE_ONLY", "PUBLIC_COST_REPORT_PENDING"})
        selected_events = list(events) if self.window is None else []
        if self.window is not None:
            by_work = {}
            for event in events:
                self.tick()
                by_work.setdefault(event["work_fingerprint"], []).append(event)
            for candidates in by_work.values():
                if any(event["accepted_at_utc"] is None for event in candidates):
                    continue
                earliest = min(event["accepted_at_utc"] for event in candidates)
                if self._selection(earliest) == "SELECTED":
                    selected_events.extend(event for event in candidates if event["accepted_at_utc"] == earliest)
        selected_works = {event["work_fingerprint"] for event in selected_events}
        selected_commits = {(event["work_fingerprint"], event["accepted_source_commit"]) for event in selected_events}
        scenarios = [scenario for scenario in proofs["handler_scenarios"] if any(
            (scenario["work_fingerprint"], witness["source_commit"]) in selected_commits for witness in scenario["witnesses"])]
        bindings = {ordinal: binding for binding in proofs["run_bindings"] for ordinal in binding["metrics_ordinals"]}
        totals = {name: _Costs() for name in ("totals", "direct", "shared", "unallocated", "retained_totals", "excluded", "unknown_time")}
        per_work = {work: _Costs() for work in selected_works}
        rows, selected_ordinals, unknown_ordinals = [], [], []
        for group in groups:
            self.tick()
            row, ordinals, run = group["row"], group["ordinals"], group["run"]
            stamp = _timestamp(row["dimensions"]["finished_at"]) if row is not None else None
            selection = self._selection(stamp)
            totals["retained_totals"].add(row)
            route, members = "UNALLOCATED", []
            if selection != "SELECTED":
                totals["unknown_time" if selection == "UNKNOWN_TIME" else "excluded"].add(row)
                if selection == "UNKNOWN_TIME":
                    unknown_ordinals.extend(ordinals)
            else:
                selected_ordinals.extend(ordinals)
                totals["totals"].add(row)
                association = {bindings[index]["work_fingerprint"] for index in ordinals if bindings[index]["state"] == "BOUND"}
                clean = run not in self.run_conflicts and all(bindings[index]["state"] != "CONFLICT" for index in ordinals)
                if clean and len(association) == 1 and association <= selected_works:
                    route, members = "DIRECT", sorted(association)
                shared = allocations.get(tuple(ordinals))
                if shared is not None:
                    if set(shared) <= selected_works:
                        route, members = "SHARED", shared
                    else:
                        route, members = "UNALLOCATED", []
                        self.limits.add("WINDOW_MEMBERSHIP_OUTSIDE_DENOMINATOR")
                totals[route.lower()].add(row)
                if members:
                    share = Fraction(1, len(members))
                    for member in members:
                        per_work[member].add(row, share)
            rows.append({"metrics_ordinals": list(ordinals), "run_id": run, "cost_row_sha256": group["sha"],
                         "window_state": selection, "allocation": route, "work_fingerprints": list(members)})
        event_unknown = self.window is not None and any(event["accepted_at_utc"] is None for event in events)
        if unknown_ordinals or event_unknown:
            self.limits.add("UNKNOWN_WINDOW_TIMESTAMPS")
        complete = not proofs["retention_limits"]["unresolved_index_ordinals"] and all(
            binding["state"] == "BOUND" for binding in proofs["run_bindings"]) and not unknown_ordinals and not event_unknown
        denominator = "COMPLETE_RETAINED_WINDOW" if complete else "PARTIAL_RETAINED_WINDOW"
        allocation_complete = bool(selected_ordinals) and not totals["unallocated"].weight and membership["state"] not in (
            "INVALID_MEMBERSHIP", "UNSUPPORTED_MEMBERSHIP")
        costs = {name: value.value() for name, value in totals.items()}
        costs.update(input_record_count=len(records), distinct_record_count=len(groups),
            duplicate_record_count=len(records) - len(groups), rows=rows,
            per_work=[{"work_fingerprint": work, "costs": per_work[work].value()} for work in sorted(per_work)],
            membership=membership, allocation_state="COMPLETE" if allocation_complete else "PARTIAL",
            legacy_observation=summarize_work_lineage_ledger(records))
        ratio = {"codex": None, "provider": None}
        count = len(selected_works)
        if count and complete and allocation_complete:
            for channel, fields in (("codex", _CODEX), ("provider", _PROVIDER)):
                if costs["totals"][channel]["full_usage"] is not None:
                    source = totals["totals"].codex if channel == "codex" else totals["totals"].provider
                    ratio[channel] = {key: _rational(source[key] / count) for key in fields}
        public_events = [{**event, "accepted_at_utc": _rational(event["accepted_at_utc"])
                          if event["accepted_at_utc"] is not None else None} for event in events]
        self.snapshot._verify()
        output = {"schema_version": 1, "state": "OBSERVATIONAL_ACCEPTED_WORK_COSTS", "snapshot": self.snapshot._metadata(),
            "window": {"kind": "RETAINED_FULL" if self.window is None else "UTC_HALF_OPEN",
                "acceptance_rule": "FIRST_RETAINED_CORROBORATED_ACCEPTANCE",
                "start_utc": self.window["start_utc"] if self.window else None,
                "end_utc": self.window["end_utc"] if self.window else None,
                "selected_ordinals": sorted(selected_ordinals), "unknown_time_ordinals": sorted(unknown_ordinals)},
            "proofs": {key: proofs[key] for key in ("works", "handler_scenarios", "run_bindings", "unknown", "retention_limits")},
            "accepted_behavior_count": count, "handler_scenario_count": len(scenarios), "denominator_state": denominator,
            "acceptance_events": public_events, "costs": costs,
            "timing": {"reported_cycle_seconds": copy.deepcopy(costs["totals"]["reported_cycle_seconds"]),
                "active_seconds": None, "quota_wait_seconds": None, "calendar_seconds": None,
                "state": "HISTORICAL_TIMING_UNVERIFIED"}, "token_per_accepted_behavior": ratio,
            "billing_savings": None, "value_state": "NOT_ESTABLISHED", "cohort_comparability": "NOT_ESTABLISHED",
            "limitations": sorted(self.limits)}
        # Reserve the owner's seven-digit8M maximum for its final identity drain.
        encoded_size = len(_canonical(output).encode("utf-8"))
        counter_growth = 7 - len(str(output["snapshot"]["bytes_read"]))
        if encoded_size + counter_growth > 2000000:
            raise _EvidenceFilesError("BYTE_LIMIT") from None
        self.tick()
        return copy.deepcopy(output)


def summarize_accepted_work(root, *, window=None):
    """Report corroborated retained work/costs after ONE successful owner exit."""
    selected_window = _window(window)
    try:
        with _proof_snapshot(root) as snapshot:
            result = snapshot._invoke(lambda: _Report(snapshot, selected_window).run())
        # Final verification adds bytes, not files/objects; no I/O after close.
        result["snapshot"]["bytes_read"] = snapshot._files._bytes_read
        return result
    except (_EvidenceFilesError, _EvidenceSnapshotError, _AcceptedWorkProofError) as error:
        pending = AcceptedWorkCostReportError(error.code)
        _copy_fixed_notes(error, pending, _ERRORS)
        raise pending from None
