"""CODEX private retained proof join; no public denominator, cost or authority API."""
from __future__ import annotations

import ast
import copy
import json
import re

from scripts.two_site_accepted_work_attestation import (
    _Attestations, _ProofIssue, _need, _hex, _identifier, _path, _canonical, _sha,
    _json, _pick, _REASONS, _ID,
    _status_metadata,
    _native_attestations,
)
from scripts.two_site_accepted_work_evidence import _EvidenceFilesError, _copy_fixed_notes
from scripts.two_site_accepted_work_snapshot import (
    _ProofSnapshot, _EvidenceSnapshotError, _ERRORS,
)
from scripts.two_site_semantic_task import validate_identity, SemanticTaskError
from scripts.two_site_source_excerpts import _split_physical_lines
from scripts.two_site_proposal_runner import SECRET
from scripts.two_site_cycle_tools import _selector_bindings, _resolve_selectors_in_source

_MARKER = "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"
_TASK = {"task_id", "goal", "context_files", "allowed_files", "requirements", "test_selectors"}
_RESULT = {"changes", "covered_requirements", "findings", "status", "summary"}
_ROOT_RESULT = {"schema_version", "state", "disposition", "category", "summary", "commit",
                "next_action", "blockers", "gemini_worker_run_id", "gemini_reviewer_run_id", "proposal_sha256"}
_PROJECTION = {"fingerprint", "gap_reference", "scenario_key", "source_sha256", "stage_id"}
_HISTORICAL = "HISTORICAL_ATTESTED_CORROBORATED"
_ORIGINAL = "ORIGINAL_ROOT_RECEIPT_CORROBORATED"
_LIMITATIONS = ("RETAINED_HISTORY_ONLY", "RETAINED_LEDGER_ONLY", "NOT_FULL_HISTORY",
    "LIFETIME_UNVERIFIED", "PRIVATE_PROOF_PHASE_ONLY", "PUBLIC_COST_REPORT_PENDING",
    "NO_BILLING_VALUE_STAGE_AUTHORITY")
_FATAL = _ERRORS | {"INVALID_CANONICAL_INDEX", "INVALID_METRICS_LEDGER"}


class _AcceptedWorkProofError(RuntimeError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in _FATAL else "GIT_FAILURE"
        super().__init__(self.code)


def _work(identity):
    return _sha(json.dumps({"kind": "two-site-logical-work", "schema_version": 1,
        **{key: identity[key] for key in ("stage_id", "gap_reference", "scenario_key", "symbol_groups")}},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"))


def _projection(identity):
    return {key: identity[key] for key in sorted(_PROJECTION)}


def _metric_identity(record):
    if type(record) is not dict:
        return False
    semantic, work = record.get("semantic_identity"), record.get("work_lineage")
    try:
        _need(type(semantic) is dict and set(semantic) == _PROJECTION and
              all(type(value) is str for value in semantic.values()))
        _hex(semantic["fingerprint"])
        _hex(semantic["source_sha256"])
        _need(type(work) is dict and set(work) == {"schema_version", "fingerprint"} and
              type(work["schema_version"]) is int and work["schema_version"] == 1)
        _hex(work["fingerprint"])
        return True
    except _ProofIssue:
        return False


def _task(value):
    _need(type(value) is dict and set(value) == _TASK, "UNSUPPORTED_SCHEMA")
    _identifier(value["task_id"])
    _need(len(value["task_id"]) <= 80 and type(value["goal"]) is str and value["goal"].strip() and
          len(value["goal"].encode("utf-8")) <= 160000)
    for field in ("context_files", "allowed_files", "requirements", "test_selectors"):
        items = value[field]
        _need(type(items) is list and 1 <= len(items) <= 32 and
              all(type(item) is str and item.strip() for item in items) and len(set(items)) == len(items))
    _need(value["allowed_files"] and value["requirements"] and value["test_selectors"])
    _need(SECRET.search(json.dumps(value, ensure_ascii=False)) is None)
    for name in value["context_files"] + value["allowed_files"]:
        _path(name)
    _need(len(set(value["context_files"] + value["allowed_files"])) <= 64)
    for selector in value["test_selectors"]:
        pieces = selector.split("::")
        _path(pieces[0])
        _need(pieces[0].startswith("tests/") and pieces[0].endswith(".py") and
              len(pieces) in (2, 3) and all(part.isidentifier() for part in pieces[1:]))
    return value


def _identity(task, context):
    goal = task["goal"]
    _need(goal.count(_MARKER) == 1, "IDENTITY_CONFLICT")
    prefix, suffix = goal.split(_MARKER)
    _need("CANONICAL_SOURCE_EXCERPTS_JSON:" not in prefix, "IDENTITY_CONFLICT")
    envelope = _json(suffix.encode("utf-8"), context.tick)
    _need(type(envelope) is dict and set(envelope) == {"head", "gap_reference", "excerpts", "semantic_identity"},
          "UNSUPPORTED_SCHEMA")
    try:
        identity = validate_identity(envelope["semantic_identity"])
    except SemanticTaskError:
        raise _ProofIssue("IDENTITY_CONFLICT") from None
    _need(envelope["gap_reference"] == identity["gap_reference"] and identity["stage_id"] == "P1-00b",
          "IDENTITY_CONFLICT")
    _hex(envelope["head"], True)
    return envelope, identity


def _source(task, envelope, identity, context, sources=None):
    parent = envelope["head"]
    manifest = identity["source_manifest"]
    expected_paths = set(task["context_files"] + task["allowed_files"])
    if sources is not None:
        _need(type(sources) is dict and set(sources) == expected_paths, "SOURCE_MISMATCH")
    excerpts = envelope["excerpts"]
    _need(type(excerpts) is list and len(excerpts) <= 32, "SOURCE_MISMATCH")
    excerpt_paths = []
    for excerpt in excerpts:
        _need(type(excerpt) is dict and set(excerpt) == {"path", "sha256", "symbols", "ranges"}, "SOURCE_MISMATCH")
        path = _path(excerpt["path"])
        _need(path.endswith(".py") and path not in excerpt_paths, "SOURCE_MISMATCH")
        excerpt_paths.append(path)
    gap_path, fragment = identity["gap_reference"].split("#")
    _need(not (set(excerpt_paths) & expected_paths), "SOURCE_MISMATCH")
    _need(set(manifest) == expected_paths | set(excerpt_paths) | {gap_path}, "SOURCE_MISMATCH")
    blobs = {}
    for path, digest in manifest.items():
        context.tick()
        blob = context.call(context.snapshot._git_blob, parent, path)
        if digest is None:
            _need(path in task["allowed_files"] and path not in task["context_files"] and
                  path not in excerpt_paths and path != gap_path and blob is None, "SOURCE_MISMATCH")
        else:
            _need(blob is not None and blob["sha256"] == digest, "SOURCE_MISMATCH")
            context.reference(path, blob["sha256"])
            blobs[path] = blob["data"]
            if path in expected_paths or path in excerpt_paths:
                try:
                    text = blob["data"].decode("utf-8", errors="strict")
                except UnicodeError:
                    raise _ProofIssue("SOURCE_MISMATCH") from None
                _need(SECRET.search(text) is None, "SOURCE_MISMATCH")
        if sources is not None and path in sources:
            item = sources[path]
            _need(type(item) is dict and set(item) == {"content", "sha256"} and item["sha256"] == digest,
                  "SOURCE_MISMATCH")
            content = item["content"]
            _need((digest is None and content is None) or
                  (type(content) is str and content.encode("utf-8") == blobs.get(path)), "SOURCE_MISMATCH")
    groups = []
    for excerpt in excerpts:
        path, symbols = excerpt["path"], excerpt["symbols"]
        ranges = excerpt["ranges"]
        _need(type(ranges) is list and all(type(item) is dict and set(item) == {"start", "end", "text"} and
              type(item["start"]) is int and type(item["end"]) is int and
              1 <= item["start"] <= item["end"] and type(item["text"]) is str for item in ranges), "SOURCE_MISMATCH")
        _need(type(symbols) is list and 1 <= len(symbols) <= 32 and
              all(type(name) is str and name.isascii() and name.isidentifier() for name in symbols) and
              len(set(symbols)) == len(symbols) and excerpt["sha256"] == manifest[path], "SOURCE_MISMATCH")
        try:
            text = blobs[path].decode("utf-8", errors="strict")
            tree, lines = ast.parse(text), _split_physical_lines(text)
        except (UnicodeError, SyntaxError, ValueError):
            raise _ProofIssue("SOURCE_MISMATCH") from None
        _need(all(item["end"] <= len(lines) for item in ranges), "SOURCE_MISMATCH")
        selected, found = [], set()
        for node in tree.body:
            context.tick()
            names = {node.name} if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else set()
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {child.id for target in targets for child in ast.walk(target)
                         if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)}
            wanted = names & set(symbols)
            if not wanted and not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            found.update(wanted)
            start = min([node.lineno] + [item.lineno for item in getattr(node, "decorator_list", [])])
            selected.append({"start": start, "end": node.end_lineno,
                             "text": "".join(lines[start - 1:node.end_lineno])})
        _need(found == set(symbols) and selected == excerpt["ranges"], "SOURCE_MISMATCH")
        groups.append({"path": path, "symbols": sorted(symbols)})
    _need(sorted(groups, key=lambda item: item["path"]) == identity["symbol_groups"], "IDENTITY_CONFLICT")
    if not gap_path.endswith(".json"):
        raise _ProofIssue("UNSUPPORTED_GAP_SCHEMA")
    gap = _json(blobs[gap_path], context.tick)
    _need(type(gap) is dict and gap.get("schema_version") == "two-server-refactor.scenario-records.v2",
          "UNSUPPORTED_GAP_SCHEMA")
    registry = gap.get("blocker_registry", {})
    _need(type(registry) is dict and type(registry.get("items")) is list, "UNSUPPORTED_GAP_SCHEMA")
    matches = [item for item in registry["items"] if type(item) is dict and item.get("blocker_id") == fragment]
    _need(len(matches) == 1 and matches[0].get("resolution_stage") == identity["stage_id"] and
          matches[0].get("severity") == "HIGH" and matches[0].get("owner") == "CODEX_FINAL_REVIEWER",
          "SOURCE_MISMATCH")


def _result(value, task, reviewer=False):
    _need(type(value) is dict and set(value) == _RESULT, "UNSUPPORTED_SCHEMA")
    code = "REVIEWER_MISMATCH" if reviewer else "PROPOSAL_MISMATCH"
    _need(value["status"] == ("VERIFIED" if reviewer else "PROPOSAL_READY") and
          type(value["summary"]) is str and value["summary"].strip() and value["findings"] == [], code)
    coverage = value["covered_requirements"]
    _need(type(coverage) is list and all(type(item) is int for item in coverage) and
          len(set(coverage)) == len(coverage) and set(coverage) == set(range(1, len(task["requirements"]) + 1)), code)
    _need(type(value["changes"]) is list and (not value["changes"] if reviewer else bool(value["changes"])), code)


def _status(value, role, run, task, parent, controller, digest, conversation=None):
    _need(type(value) is dict, "REVIEWER_MISMATCH")
    _status_metadata(value)
    expected = {"role": role, "run_id": run, "task_id": task["task_id"], "head": parent,
                "controller_run_id": controller, "result_sha256": digest,
                "state": "VERIFIED" if role == "verifier" else "PROPOSAL_READY",
                "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE"}
    _need(all(value.get(key) == item for key, item in expected.items()), "REVIEWER_MISMATCH")
    cid = value.get("conversation_id")
    _need(type(cid) is str and 0 < len(cid.encode("utf-8")) <= 128 and
          not any(ord(c) < 32 or ord(c) == 127 for c in cid) and
          (conversation is None or cid == conversation), "REVIEWER_MISMATCH")
    profile = value.get("runtime_profile")
    if profile is not None:
        _need(type(profile) is dict and profile.get("conversation_id") == cid and
              type(profile.get("actual_executor_checked")) is bool,
              "REVIEWER_MISMATCH")
    for key in ("response_schema_sha256", "review_transport_sha256"):
        if key in value:
            _hex(value[key])
    return cid


def _native(value, task, proposal):
    _need(type(value) is dict and set(value) == {"state", "proposal_sha256", "selectors", "unsupported_specs",
        "returncode", "stdout_tail", "stderr_tail", "snapshot_files", "snapshot_bytes", "isolation"}, "UNSUPPORTED_SCHEMA")
    _need(value["state"] == "PASSED" and type(value["returncode"]) is int and value["returncode"] == 0 and
          value["proposal_sha256"] == proposal and value["selectors"] == task["test_selectors"] and
          value["unsupported_specs"] == [] and value["isolation"] == "bwrap:ro-source,empty-home,clearenv,unshare-all",
          "NATIVE_MISMATCH")
    _need(all(type(value[key]) is int and value[key] >= 0 for key in ("snapshot_files", "snapshot_bytes")) and
          all(type(value[key]) is str and len(value[key].encode("utf-8")) <= 8192 for key in ("stdout_tail", "stderr_tail")),
          "NATIVE_MISMATCH")


def _selector(blob, selector):
    try:
        tree = ast.parse(blob.decode("utf-8", errors="strict"))
        _need(_resolve_selectors_in_source(_selector_bindings(tree), [selector], selector.split("::")[0]),
              "SOURCE_MISMATCH")
    except (SyntaxError, UnicodeError, ValueError):
        raise _ProofIssue("SOURCE_MISMATCH") from None


class _Collector:
    def __init__(self, snapshot):
        self.context = _Attestations(snapshot)
        self.snapshot = snapshot
        self.records = []
        self.unknown = []
        self.limitations = set(_LIMITATIONS)
        self.partial = {}

    def _metric(self, row, task, identity, parent, commit, controller, worker, reviewer, proposal):
        matches = [(i, item) for i, item in enumerate(self.records)
                   if type(item) is dict and item.get("run_id") == controller]
        _need(matches, "ACCEPTANCE_MISMATCH")
        canonical_task = _sha(json.dumps(task, sort_keys=True).encode("utf-8"))
        expected = {"run_id": controller, "head_before": parent, "head_after": commit,
            "gemini_worker_run_id": worker, "gemini_reviewer_run_id": reviewer,
            "proposal_sha256": proposal, "task_id": task["task_id"], "task_sha256": canonical_task,
            "gap_reference": identity["gap_reference"], "semantic_identity": _projection(identity),
            "work_lineage": {"schema_version": 1, "fingerprint": _work(identity)}}
        for _, item in matches:
            _need(_metric_identity(item) and item.get("state") in ("CONTINUE", "COMPLETE") and
                  all(item.get(key) == value for key, value in expected.items()), "ACCEPTANCE_MISMATCH")
        return [i for i, _ in matches]

    def _roots(self, att, parent, commit, controller, worker, reviewer, proposal):
        supers = [actor["supervisor_acceptance"] for actor in att["actor"] if "supervisor_acceptance" in actor]
        if not supers:
            self.limitations.add("ORIGINAL_ROOT_NOT_DECLARED")
            return _HISTORICAL
        _need(all(type(item) is dict and item == supers[0] for item in supers), "ACCEPTANCE_MISMATCH")
        supervisor = supers[0]
        _need(supervisor.get("cycle_id") == controller and supervisor.get("state") in ("CONTINUE", "COMPLETE") and
              supervisor.get("head_before") == parent and supervisor.get("head_after") == commit,
              "ACCEPTANCE_MISMATCH")
        hashes = [supervisor.get(key) for key in ("result_sha256", "status_sha256")]
        if all(value is None for value in hashes):
            self.limitations.add("ORIGINAL_ROOT_NOT_DECLARED")
            return _HISTORICAL
        for digest in hashes:
            _hex(digest)
        complete = False
        for layout in ("runs", "flat"):
            values = {}
            for kind, digest in zip(("root_result", "root_status"), hashes):
                try:
                    body = self.context.read(kind, (layout, controller))
                except _ProofIssue as error:
                    if error.code != "MISSING_ARTIFACT":
                        raise
                    continue
                _need(_sha(body) == digest, "HASH_MISMATCH")
                values[kind] = _json(body, self.context.tick)
            result, status = values.get("root_result"), values.get("root_status")
            if result is not None:
                _need(type(result) is dict and set(result) == _ROOT_RESULT and result["schema_version"] == "1.0" and
                      result["state"] in ("CONTINUE", "COMPLETE") and result["disposition"] == "NONE" and
                      result["category"] == "NONE" and result["blockers"] == [] and result["commit"] == commit and
                      result["gemini_worker_run_id"] == worker and result["gemini_reviewer_run_id"] == reviewer and
                      result["proposal_sha256"] == proposal and result["state"] == supervisor["state"] and
                      type(result["summary"]) is str and bool(result["summary"].strip()) and
                      (result["next_action"] is None or type(result["next_action"]) is str and bool(result["next_action"].strip())) and
                      (result["state"] != "CONTINUE" or result["next_action"] is not None), "ACCEPTANCE_MISMATCH")
            if status is not None:
                expected = {"state": supervisor["state"], "head_before": parent, "head_after": commit,
                    "gemini_worker_run_id": worker, "gemini_reviewer_run_id": reviewer,
                    "proposal_sha256": proposal, "blockers": []}
                _need(type(status) is dict and all(status.get(key) == value for key, value in expected.items()),
                      "ACCEPTANCE_MISMATCH")
                _need("schema_version" not in status or status["schema_version"] == "1.0", "UNSUPPORTED_SCHEMA")
                _need("summary" not in status or type(status["summary"]) is str and status["summary"].strip(),
                      "ACCEPTANCE_MISMATCH")
                if "next_action" in status:
                    action = status["next_action"]
                    _need((action is None or type(action) is str and action.strip()) and
                          (status["state"] != "CONTINUE" or action is not None), "ACCEPTANCE_MISMATCH")
                if result is not None:
                    _need(all(key not in status or status[key] == result[key] for key in ("summary", "next_action")),
                          "ACCEPTANCE_MISMATCH")
                optional = {"run_id": controller, "task_id": _pick(att["source"] + att["actor"],
                    "source_task_id", "generation_task_id"), "result_sha256": hashes[0], "head_changed": True}
                _need(all(key not in status or type(status[key]) is type(value) and status[key] == value
                          for key, value in optional.items()), "ACCEPTANCE_MISMATCH")
                if "protected_refs" in status:
                    refs = status["protected_refs"]
                    _need(type(refs) is dict and set(refs) == set(self.snapshot._refs), "ACCEPTANCE_MISMATCH")
                    for digest in refs.values():
                        _hex(digest, True)
            complete |= result is not None and status is not None
        if not complete:
            self.limitations.add("MISSING_ORIGINAL_ROOT_RECEIPTS")
        return _ORIGINAL if complete else _HISTORICAL

    def chain(self, row):
        context = self.context
        att = context.attestation(row)
        sources, actors = att["source"], att["actor"]
        controller = _identifier(_pick(actors, "controller_run_id"))
        worker_actor, reviewer_actor = _pick(actors, "worker"), _pick(actors, "reviewer")
        _need(type(worker_actor) is dict and type(reviewer_actor) is dict)
        _need(all(type(actor.get("conversation_id")) is str and actor["conversation_id"]
                  for actor in (worker_actor, reviewer_actor)), "REVIEWER_MISMATCH")
        worker, reviewer = _identifier(worker_actor.get("run_id")), _identifier(reviewer_actor.get("run_id"))
        _need(worker.endswith("-worker-0") and reviewer == worker[:-9] + "-reviewer-0" and worker != reviewer,
              "UNSUPPORTED_SCHEMA")
        task_id = _pick(sources + actors, "source_task_id", "generation_task_id")
        _identifier(task_id)
        parent = _hex(_pick(sources + att["git"], "source_head", "accepted_parent"), True)
        commit = _hex(_pick(sources + att["git"], "accepted_source_commit", "accepted_commit"), True)
        proposal_hash = _hex(_pick(sources + actors, "source_proposal_sha256", "proposal_sha256"))
        _need(commit == row["source_commit"], "SOURCE_MISMATCH")
        for scope in att["scope"]:
            if "scenarios_covered" in scope:
                _need(type(scope["scenarios_covered"]) is list and row["scenario_key"] in scope["scenarios_covered"],
                      "REFERENCE_CONFLICT")
            if "reconciled_scope" in scope:
                reconciled = scope["reconciled_scope"]
                _need(type(reconciled) is dict and reconciled.get("reconciled_source_commit") == commit and
                      type(reconciled.get("scenarios_reconciled")) is list and
                      row["scenario_key"] in reconciled["scenarios_reconciled"], "REFERENCE_CONFLICT")
        original_raw = context.read("task", task_id)
        task = _task(_json(original_raw, context.tick))
        _need(task["task_id"] == task_id)
        declared = _pick(actors, "generation_task_sha256", required=False)
        _need(declared is None or declared == _sha(original_raw), "HASH_MISMATCH")
        worker_raw = context.read("proposal_snapshot", worker)
        ws = _json(worker_raw, context.tick)
        _need(type(ws) is dict and set(ws) == {"task", "source_head", "sources"} and
              ws["task"] == task and ws["source_head"] == parent, "SOURCE_MISMATCH")
        _need(len(json.dumps({"task": task, "sources": ws["sources"]}).encode()) <= 160000, "SOURCE_MISMATCH")
        declared = _pick(sources, "source_task_snapshot_sha256", required=False)
        _need(declared is None or declared == _sha(worker_raw), "HASH_MISMATCH")
        envelope, identity = _identity(task, context)
        work = _work(identity)
        self.partial[row["index_ordinal"]] = (work, identity["fingerprint"], controller)
        _need(envelope["head"] == parent, "SOURCE_MISMATCH")
        _source(task, envelope, identity, context, ws["sources"])
        for source in sources:
            for name in ("generation_source_manifest", "source_manifest_sha256"):
                if name in source:
                    subset = source[name]
                    _need(type(subset) is dict and subset and all(path in identity["source_manifest"] and
                          digest == identity["source_manifest"][path] for path, digest in subset.items()), "SOURCE_MISMATCH")
        symbol_path, symbol = row["canonical_symbol"].split(":")
        _need(any(group["path"] == symbol_path and symbol in group["symbols"] for group in identity["symbol_groups"]),
              "SOURCE_MISMATCH")
        _need(row["selector"] in task["test_selectors"] and
              att["receipt"]["canonical_gap"] == identity["gap_reference"].split("#")[1], "SOURCE_MISMATCH")
        proposal_raw = context.read("worker_result", worker)
        _need(_sha(proposal_raw) == proposal_hash, "HASH_MISMATCH")
        proposal = _json(proposal_raw, context.tick)
        _result(proposal, task)
        _need(context.call(self.snapshot._git_commit, commit)["parents"] == (parent,) and
              context.call(self.snapshot._is_ancestor, commit, context.head), "SOURCE_MISMATCH")
        changes = {}
        for change in proposal["changes"]:
            _need(type(change) is dict and set(change) == {"path", "original_sha256", "content"}, "PROPOSAL_MISMATCH")
            path = _path(change["path"])
            _need(path in task["allowed_files"] and path not in changes and
                  type(change["content"]) is str and change["content"].strip() and "\0" not in change["content"],
                  "PROPOSAL_MISMATCH")
            _need(SECRET.search(change["content"]) is None, "PROPOSAL_MISMATCH")
            before = ws["sources"][path]
            _need(change["original_sha256"] == before["sha256"] and change["content"] != before["content"], "PROPOSAL_MISMATCH")
            after = context.blob(commit, path)
            _need(after["data"] == change["content"].encode("utf-8"), "PROPOSAL_MISMATCH")
            changes[path] = after
        diff = context.call(self.snapshot._git_changes, commit)
        _need({item["path"] for item in diff} == set(changes), "PROPOSAL_MISMATCH")
        for item in diff:
            before = context.call(self.snapshot._git_blob, parent, item["path"])
            _need(item["new_oid"] == changes[item["path"]]["git_oid"] and
                  item["new_mode"] == changes[item["path"]]["mode"] and
                  item["old_oid"] == (before["git_oid"] if before else "0" * 40) and
                  item["old_mode"] == (before["mode"] if before else "000000"), "PROPOSAL_MISMATCH")
        test_path = row["selector"].split("::")[0]
        _need(test_path in changes and changes[test_path]["sha256"] == row["source_file_sha256"], "PROPOSAL_MISMATCH")
        for declaration in att["git"]:
            expected = {"single_accepted_file": test_path, "current_file_sha256": row["source_file_sha256"],
                        "file_mode": changes[test_path]["mode"], "current_file_utf8_bytes": len(changes[test_path]["data"])}
            _need(all(key not in declaration or declaration[key] == value for key, value in expected.items()), "SOURCE_MISMATCH")
        for source in sources:
            for name in ("accepted_file", "accepted_test_file"):
                if name in source:
                    declaration = source[name]
                    _need(type(declaration) is dict and declaration.get("path") == test_path and
                          declaration.get("sha256") == row["source_file_sha256"] and
                          ("mode" not in declaration or declaration["mode"] == changes[test_path]["mode"]) and
                          ("utf8_bytes" not in declaration or type(declaration["utf8_bytes"]) is int and
                           declaration["utf8_bytes"] == len(changes[test_path]["data"])), "SOURCE_MISMATCH")
        _selector(changes[test_path]["data"], row["selector"])
        worker_status = context.value("proposal_status", worker)
        _need("result_sha256" not in worker_actor or worker_actor["result_sha256"] == proposal_hash,
              "REVIEWER_MISMATCH")
        worker_cid = _status(worker_status, "worker", worker, task, parent, controller,
                             proposal_hash, worker_actor.get("conversation_id"))
        native_raw = context.read("native", (worker[:-9], 0))
        native = _json(native_raw, context.tick)
        _native(native, task, proposal_hash)
        _native_attestations(att["documents"], _sha(native_raw), proposal_hash, row["selector"], native["isolation"])
        reviewer_raw = context.read("reviewer_result", reviewer)
        reviewer_result = _json(reviewer_raw, context.tick)
        _result(reviewer_result, task, True)
        reviewer_status = context.value("proposal_status", reviewer)
        reviewer_cid = _status(reviewer_status, "verifier", reviewer, task, parent, controller,
                               _sha(reviewer_raw), reviewer_actor.get("conversation_id"))
        _need(reviewer_cid != worker_cid and reviewer_actor.get("state") == "VERIFIED" and
              reviewer_actor.get("findings") == [] and worker_actor.get("state") == "PROPOSAL_READY" and
              ("role" not in reviewer_actor or reviewer_actor["role"] == "verifier") and
              reviewer_actor.get("result_sha256") == _sha(reviewer_raw), "REVIEWER_MISMATCH")
        expected = {"input_proposal_sha256": proposal_hash, "input_test_evidence_sha256": _sha(native_raw),
                    "input_worker_run_id": worker, "input_worker_conversation_id": worker_cid}
        _need(all(reviewer_status.get(key) == value for key, value in expected.items()), "REVIEWER_MISMATCH")
        _hex(reviewer_status.get("review_transport_sha256"))
        vs = context.value("proposal_snapshot", reviewer)
        _need(type(vs) is dict and set(vs) == {"task", "source_head", "sources", "proposal", "test_evidence"} and
              vs["task"] == task and vs["source_head"] == parent and vs["sources"] == ws["sources"] and
              type(vs["proposal"]) is str and _sha(vs["proposal"].encode("utf-8")) == proposal_hash and
              _json(vs["proposal"].encode("utf-8"), context.tick) == proposal and vs["test_evidence"] == native,
              "REVIEWER_MISMATCH")
        ordinals = self._metric(row, task, identity, parent, commit, controller, worker, reviewer, proposal_hash)
        tier = self._roots(att, parent, commit, controller, worker, reviewer, proposal_hash)
        return {"work": work, "identity": identity, "parent": parent, "commit": commit, "row": row,
                "controller": controller, "ordinals": ordinals, "tier": tier, "refs": context.proof_refs()}

    def unknown_record(self, reason, rows=(), ordinals=(), work=None, semantic=None, runs=(), refs=()):
        references = {_canonical(item): item for item in refs}
        if hasattr(self, "metric_ref"):
            references[_canonical(self.metric_ref)] = self.metric_ref
        self.unknown.append({"work_fingerprint": work, "semantic_fingerprint": semantic,
            "reason_code": reason, "proof_refs": sorted(references.values(),
                key=lambda item: (item["path"], item["sha256"], item["pointer"])), "retained_run_ids": sorted(set(runs)),
            "metrics_ordinals": sorted(set(ordinals)), "index_ordinals": sorted(set(rows))})

    def run(self):
        try:
            return self._run()
        except (MemoryError, RecursionError):
            raise _EvidenceFilesError("COUNT_LIMIT") from None

    def _run(self):
        context = self.context
        try:
            rows = context.index()
        except _ProofIssue:
            raise _AcceptedWorkProofError("INVALID_CANONICAL_INDEX") from None
        try:
            self.records = self.snapshot._json_list("metrics")
        except _EvidenceSnapshotError as error:
            if error.code == "JSON_INVALID":
                raise _AcceptedWorkProofError("INVALID_METRICS_LEDGER") from None
            raise
        metrics_raw = context.read("metrics")
        metric_ref = {"path": ".local/two-site-refactor/autopilot/metrics.json", "sha256": _sha(metrics_raw), "pointer": ""}
        self.metric_ref = metric_ref
        proven = []
        for row in rows:
            context.tick()
            try:
                proof = self.chain(row)
                proof["refs"].append(metric_ref)
                proven.append(proof)
            except _ProofIssue as error:
                work, semantic, run = self.partial.get(row["index_ordinal"], (None, None, None))
                ordinals = [i for i, record in enumerate(self.records)
                            if run is not None and type(record) is dict and record.get("run_id") == run]
                self.unknown_record(error.code, [row["index_ordinal"]], ordinals, work, semantic,
                                    [run] if run else [], context.proof_refs())
        return self.aggregate(rows, proven)

    def aggregate(self, rows, proven):
        by_work, by_semantic, projections, run_groups = {}, {}, {}, {}
        for proof in proven:
            by_work.setdefault(proof["work"], []).append(proof)
            by_semantic.setdefault(proof["identity"]["fingerprint"], set()).add(proof["work"])
            projections[proof["identity"]["fingerprint"]] = _projection(proof["identity"])
        for ordinal, record in enumerate(self.records):
            run = record.get("run_id") if type(record) is dict else None
            run = run if type(run) is str and _ID.fullmatch(run) else None
            run_groups.setdefault((run, None if run is not None else ordinal), []).append(ordinal)
        conflicts, edges, bindings, alias_refs, binding_refs = set(), {}, [], {}, {}
        commit_owners = {}
        for proof in proven:
            commit_owners.setdefault(proof["commit"], set()).add(proof["work"])
        contradictions = _REASONS - {"MISSING_ARTIFACT", "UNSUPPORTED_SCHEMA", "UNSUPPORTED_GAP_SCHEMA", "UNBOUND_METRICS"}
        for unknown in self.unknown:
            if unknown["reason_code"] in contradictions:
                if unknown["work_fingerprint"] is not None:
                    conflicts.add(unknown["work_fingerprint"])
                for ordinal in unknown["index_ordinals"]:
                    conflicts.update(commit_owners.get(rows[ordinal]["source_commit"], ()))
        scenario_owners = {}
        for proof in proven:
            row = proof["row"]
            key = (proof["identity"]["gap_reference"], row["canonical_symbol"], row["scenario_key"])
            scenario_owners.setdefault(key, set()).add(proof["work"])
        for owners in [*by_semantic.values(), *scenario_owners.values()]:
            if len(owners) > 1:
                conflicts.update(owners)
        fields = ("state", "head_before", "head_after", "task_id", "task_sha256", "gap_reference",
                  "semantic_identity", "work_lineage", "gemini_worker_run_id", "gemini_reviewer_run_id", "proposal_sha256")
        for (run, _), ordinals in run_groups.items():
            self.context.tick()
            variants, claims, semantics = [], set(), set()
            contradictory = False
            for ordinal in ordinals:
                record = self.records[ordinal]
                if type(record) is not dict:
                    continue
                semantic, work = record.get("semantic_identity"), record.get("work_lineage")
                if type(semantic) is dict and type(semantic.get("fingerprint")) is str:
                    semantics.add(semantic["fingerprint"])
                    claims.update(by_semantic.get(semantic["fingerprint"], ()))
                if type(work) is dict and type(work.get("fingerprint")) is str and work["fingerprint"] in by_work:
                    claims.add(work["fingerprint"])
                if _metric_identity(record) and semantic["fingerprint"] in projections:
                    contradictory |= (semantic != projections[semantic["fingerprint"]] or
                                      work["fingerprint"] not in by_semantic[semantic["fingerprint"]])
                variants.append(_canonical({key: record.get(key) for key in fields}))
            for key in claims:
                edges.setdefault(key, set()).update(claims)
            bad = contradictory or len(claims) > 1 or len(set(variants)) > 1 and bool(claims)
            bound_work, bound_semantic = None, None
            if run is not None and claims and not bad and all(_metric_identity(self.records[i]) for i in ordinals):
                record = self.records[ordinals[0]]
                try:
                    if record.get("task_id") is not None:
                        _identifier(record["task_id"])
                        _need(len(record["task_id"]) <= 80, "IDENTITY_CONFLICT")
                    if record.get("task_sha256") is not None:
                        _hex(record["task_sha256"])
                    if record.get("head_before") is not None:
                        _hex(record["head_before"], True)
                    _need(record.get("gap_reference") is None or
                          record["gap_reference"] == record["semantic_identity"]["gap_reference"], "IDENTITY_CONFLICT")
                except _ProofIssue:
                    bad = True
                complete = all(record.get(key) is not None for key in
                               ("task_id", "task_sha256", "head_before", "gap_reference"))
            else:
                complete = False
            if complete and not bad:
                self.context.refs = set()
                try:
                    task = _task(self.context.value("task", _identifier(record.get("task_id"))))
                    _need(_sha(json.dumps(task, sort_keys=True).encode("utf-8")) == record.get("task_sha256"), "IDENTITY_CONFLICT")
                    envelope, identity = _identity(task, self.context)
                    bound_work, bound_semantic = _work(identity), identity["fingerprint"]
                    _need(bound_work in claims and record.get("semantic_identity") == _projection(identity) and
                          record.get("work_lineage") == {"schema_version": 1, "fingerprint": bound_work} and
                          record.get("gap_reference") == identity["gap_reference"] and
                          envelope["head"] == record.get("head_before"), "IDENTITY_CONFLICT")
                    _source(task, envelope, identity, self.context)
                except _ProofIssue as error:
                    if error.code != "MISSING_ARTIFACT":
                        bad = True
                    bound_work = bound_semantic = None
                binding_refs[ordinals[0]] = self.context.proof_refs()
                if bound_work is not None:
                    alias_refs.setdefault(bound_work, []).extend(self.context.proof_refs())
            if bad:
                conflicts.update(claims)
            state = "CONFLICT" if bad else "BOUND" if bound_work else "UNBOUND"
            bindings.append({"run_id": run, "metrics_ordinals": ordinals,
                "semantic_fingerprint": bound_semantic, "work_fingerprint": bound_work, "state": state})
        pending = list(conflicts)
        while pending:
            for neighbour in edges.get(pending.pop(), ()):
                if neighbour not in conflicts:
                    conflicts.add(neighbour)
                    pending.append(neighbour)
        works, scenarios, proved = [], {}, set()
        for fingerprint, proofs in sorted(by_work.items()):
            if fingerprint in conflicts:
                for proof in proofs:
                    self.unknown_record("IDENTITY_CONFLICT", [proof["row"]["index_ordinal"]], proof["ordinals"],
                        fingerprint, proof["identity"]["fingerprint"], [proof["controller"]], proof["refs"])
                continue
            revisions, refs = {}, {}
            for reference in alias_refs.get(fingerprint, ()):
                refs[_canonical(reference)] = reference
            for proof in proofs:
                row, identity = proof["row"], proof["identity"]
                proved.add(row["index_ordinal"])
                revision = {"semantic_fingerprint": identity["fingerprint"], "source_sha256": identity["source_sha256"],
                            "source_head": proof["parent"], "accepted_source_commit": proof["commit"]}
                revisions[_canonical(revision)] = revision
                for reference in proof["refs"]:
                    refs[_canonical(reference)] = reference
                key = identity["gap_reference"], row["canonical_symbol"], row["scenario_key"]
                scenario = scenarios.setdefault(key, {"work_fingerprint": fingerprint,
                    "gap_reference": key[0], "canonical_symbol": key[1], "scenario_key": key[2], "witnesses": {}})
                witness = {"selector": row["selector"], "source_commit": row["source_commit"],
                           "source_file_sha256": row["source_file_sha256"]}
                scenario["witnesses"][_canonical(witness)] = witness
            works.append({"work_fingerprint": fingerprint,
                "semantic_fingerprints": sorted({proof["identity"]["fingerprint"] for proof in proofs}),
                "source_revisions": [revisions[key] for key in sorted(revisions)],
                "evidence_level": _ORIGINAL if any(proof["tier"] == _ORIGINAL for proof in proofs) else _HISTORICAL,
                "proof_refs": sorted(refs.values(), key=lambda value: (value["path"], value["sha256"], value["pointer"])),
                "retained_run_ids": sorted(binding["run_id"] for binding in bindings
                    if binding["work_fingerprint"] == fingerprint and binding["state"] == "BOUND")})
        for binding in bindings:
            if binding["work_fingerprint"] in conflicts:
                binding["state"] = "CONFLICT"
            if binding["state"] != "BOUND":
                self.unknown_record("IDENTITY_CONFLICT" if binding["state"] == "CONFLICT" else "UNBOUND_METRICS",
                    ordinals=binding["metrics_ordinals"], work=binding["work_fingerprint"],
                    semantic=binding["semantic_fingerprint"], runs=[binding["run_id"]] if binding["run_id"] else [],
                    refs=binding_refs.get(binding["metrics_ordinals"][0], ()))
        for scenario in scenarios.values():
            scenario["witnesses"] = [scenario["witnesses"][key] for key in sorted(scenario["witnesses"])]
        if len(proved) != len(rows):
            self.limitations.add("INCOMPLETE_ACCEPTANCE")
        if any(binding["state"] != "BOUND" for binding in bindings):
            self.limitations.add("UNKNOWN_RETAINED_ROWS")
        unique_unknown = {_canonical(item): item for item in self.unknown}
        self.snapshot._verify()
        output = {"schema_version": 1, "state": "OBSERVATIONAL_PROOF_JOIN", "snapshot": self.snapshot._metadata(),
            "works": works, "handler_scenarios": [scenarios[key] for key in sorted(scenarios)],
            "run_bindings": sorted(bindings, key=lambda value: (value["metrics_ordinals"][0], value["run_id"] or "")),
            "unknown": [unique_unknown[key] for key in sorted(unique_unknown)],
            "retention_limits": {"index_row_count": len(rows), "metrics_record_count": len(self.records),
                "proved_index_ordinals": sorted(proved), "unresolved_index_ordinals": sorted(set(range(len(rows))) - proved),
                "limitations": sorted(self.limitations)}}
        if len(_canonical(output).encode("utf-8")) > 2000000:
            raise _EvidenceFilesError("BYTE_LIMIT") from None
        self.context.tick()
        return copy.deepcopy(output)


def _collect_accepted_work_proofs(snapshot):
    """Use one LIVE owner; returned observations remain conditional on normal exit."""
    if type(snapshot) is not _ProofSnapshot:
        raise _AcceptedWorkProofError("CLOSED_SNAPSHOT") from None
    try:
        return snapshot._invoke(lambda: _Collector(snapshot).run())
    except (_EvidenceFilesError, _EvidenceSnapshotError) as error:
        pending = _AcceptedWorkProofError(error.code)
        _copy_fixed_notes(error, pending, _FATAL)
        raise pending from None
