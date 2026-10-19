"""CODEX private canonical locators and bounded attestation data, not acceptance."""
from __future__ import annotations

import hashlib
import json
import math
import re

from scripts.two_site_accepted_work_evidence import _artifact, _EvidenceFilesError
from scripts.two_site_accepted_work_snapshot import _EvidenceSnapshotError

_LEDGER = "docs/refactor/two-server/section-1/00-stage-ledger.md"
_INVENTORY = "docs/refactor/two-server/section-1/inventory/"
_HEADING = "Reviewed current seven-scenario Source index (data):"
_FIELDS = ("seed_id", "canonical_symbol", "scenario_key", "selector", "source_commit",
           "source_file_sha256", "receipt_path", "receipt_sha256", "handler_pointer",
           "evidence_limits_path", "evidence_limits_sha256", "evidence_limits_pointer")
_REASONS = frozenset({"MISSING_ARTIFACT", "MALFORMED_EVIDENCE", "HASH_MISMATCH",
    "REFERENCE_CONFLICT", "IDENTITY_CONFLICT", "SOURCE_MISMATCH", "PROPOSAL_MISMATCH",
    "NATIVE_MISMATCH", "REVIEWER_MISMATCH", "ACCEPTANCE_MISMATCH", "UNSUPPORTED_SCHEMA",
    "UNSUPPORTED_GAP_SCHEMA", "UNBOUND_METRICS"})
_HEX = re.compile(r"[0-9a-f]{64}", re.ASCII)
_COMMIT = re.compile(r"[0-9a-f]{40}", re.ASCII)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", re.ASCII)
_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,249}\.json", re.ASCII)


class _ProofIssue(Exception):
    def __init__(self, code):
        self.code = code if code in _REASONS else "MALFORMED_EVIDENCE"
        super().__init__(self.code)


def _need(value, code="MALFORMED_EVIDENCE"):
    if not value:
        raise _ProofIssue(code) from None


def _hex(value, commit=False):
    _need(type(value) is str and (_COMMIT if commit else _HEX).fullmatch(value))
    return value


def _identifier(value):
    _need(type(value) is str and _ID.fullmatch(value))
    return value


def _path(value):
    _need(type(value) is str and 0 < len(value.encode("utf-8")) <= 1024)
    parts = value.split("/")
    _need(not value.startswith("/") and not any(not p or p in (".", "..") or
          p.lower() == ".git" or p.startswith("-") for p in parts))
    _need(not any(c in value for c in "\\\0:*?[]{}") and
          not any(ord(c) < 32 or ord(c) == 127 for c in value))
    return value


def _receipt_path(value):
    _path(value)
    _need(value.startswith(_INVENTORY) and _BASENAME.fullmatch(value[len(_INVENTORY):]),
          "REFERENCE_CONFLICT")
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result)
        result[key] = value
    return result


def _constant(_):
    raise _ProofIssue("MALFORMED_EVIDENCE")


def _json(body, tick):
    """Strict bounded pure parser; CPU work shares the owner's deadline."""
    try:
        text = body.decode("utf-8", errors="strict")
        depth, nodes, quoted, escaped, scalar = 0, 0, False, False, False
        for index, char in enumerate(text):
            if index % 1024 == 0:
                tick()
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
                continue
            if char == '"':
                quoted, scalar = True, False
                nodes += 1
            elif char in "[{":
                depth += 1
                nodes += 1
                scalar = False
                _need(depth <= 32)
            elif char in "]}":
                depth -= 1
                scalar = False
            elif char in ",:" or char.isspace():
                scalar = False
            elif not scalar:
                scalar = True
                nodes += 1
            _need(nodes <= 100000)
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
        pending, nodes = [(value, 0)], 0
        while pending:
            tick()
            item, depth = pending.pop()
            nodes += 1
            _need(depth <= 32 and nodes <= 100000)
            if type(item) is dict:
                nodes += len(item)
                for key in item:
                    key.encode("utf-8", errors="strict")
                pending.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                pending.extend((child, depth + 1) for child in item)
            elif type(item) is str:
                item.encode("utf-8", errors="strict")
            elif type(item) is float:
                _need(math.isfinite(item))
        tick()
        return value
    except (_EvidenceFilesError, _EvidenceSnapshotError):
        raise
    except MemoryError:
        raise _EvidenceFilesError("COUNT_LIMIT") from None
    except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        raise _ProofIssue("MALFORMED_EVIDENCE") from None


def _pick(sections, *names, required=True):
    found = [section[name] for section in sections for name in names if name in section]
    _need(not required or found, "MISSING_ARTIFACT")
    if found:
        _need(all(value == found[0] for value in found), "REFERENCE_CONFLICT")
        return found[0]
    return None


def _status_metadata(value):
    """Bound only demonstrated optional status profiles, never CLI/auth I/O."""
    code = "REVIEWER_MISMATCH"
    if "schema_version" in value:
        raise _ProofIssue("UNSUPPORTED_SCHEMA")
    strings = ("authority", "controller_run_id", "conversation_id", "effort", "head", "model",
               "response_schema_sha256", "result_sha256", "role", "run_id", "state", "task_id")
    for key in strings:
        if key in value:
            _need(type(value[key]) is str and len(value[key].encode("utf-8")) <= 8192, code)
    for key in ("duration_seconds", "retain_until", "started_at"):
        if key in value:
            _need(type(value[key]) in (int, float) and
                  (type(value[key]) is int or math.isfinite(value[key])), code)
    if "cli_pid" in value:
        _need(type(value["cli_pid"]) is int, code)
    profiles = {
        "cli": {"path": str, "sha256": str, "version": str},
        "cli_source": {"sha256": str, "source_path": str},
        "runtime_profile": {"actual_executor_checked": bool, "command_policy": str,
                            "conversation_id": str, "name": str, "native_tools": int},
        "session_gate": {"activation_reason": str, "query_operation_count": int,
                         "required_symbol_count": int, "root_count": int, "state": str},
        "usage": {key: int for key in ("cache_read_tokens", "input_tokens", "output_tokens",
                                       "thinking_tokens", "total_tokens")},
    }
    for key, fields in profiles.items():
        if key not in value:
            continue
        profile = value[key]
        if profile is None and key in {"usage", "runtime_profile"}:
            continue
        _need(type(profile) is dict and set(profile) == set(fields), "UNSUPPORTED_SCHEMA")
        for name, kind in fields.items():
            item = profile[name]
            _need(type(item) is kind, code)
            if kind is str:
                _need(len(item.encode("utf-8")) <= 8192, code)
            elif kind is int:
                _need(item >= 0, code)
        if "sha256" in profile:
            _hex(profile["sha256"])


def _native_attestations(documents, native_sha, proposal_sha, selector, isolation):
    for _, _, document in documents:
        if "test_witness_evidence" not in document:
            continue
        evidence = document["test_witness_evidence"]
        _need(type(evidence) is dict, "NATIVE_MISMATCH")
        for name in ("candidate_test_evidence", "historical"):
            if name not in evidence:
                continue
            witness = evidence[name]
            _need(type(witness) is dict, "NATIVE_MISMATCH")
            _need({"state", "returncode"} <= set(witness) and
                  (({"sha256", "selector"} <= set(witness)) if name == "candidate_test_evidence" else
                   ("sha256" in witness or "input_test_evidence_sha256" in witness)), "NATIVE_MISMATCH")
            expected = {"sha256": native_sha, "input_test_evidence_sha256": native_sha,
                        "proposal_sha256": proposal_sha, "selector": selector,
                        "isolation": isolation, "state": "PASSED", "returncode": 0}
            _need(all(key not in witness or type(witness[key]) is type(value) and witness[key] == value
                      for key, value in expected.items()), "NATIVE_MISMATCH")
            if "passed_tests" in witness:
                _need(type(witness["passed_tests"]) is int and witness["passed_tests"] >= 0, "NATIVE_MISMATCH")
        if "live_witness_selectors" in evidence:
            selectors = evidence["live_witness_selectors"]
            _need(type(selectors) is list and selector in selectors, "NATIVE_MISMATCH")


class _Attestations:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.head = snapshot._metadata()["head"]
        self.refs = set()
        self.cache = {}
        self.traversals = 0
        self.ledger_sha = None
        self.index_sha = None

    def tick(self):
        self.snapshot._files._check_time()

    def charge(self, tag, text):
        self.snapshot._files._charge_metadata(("proof", tag, text), len(text.encode("utf-8")))

    def reference(self, path, digest, pointer=""):
        _path(path)
        _hex(digest)
        _need(type(pointer) is str and len(pointer.encode("utf-8")) <= 1024 and
              not any(ord(char) < 32 or ord(char) == 127 for char in pointer), "REFERENCE_CONFLICT")
        self.charge("reference", path + "#" + pointer)
        self.refs.add((path, digest, pointer))

    def proof_refs(self):
        return [{"path": path, "sha256": digest, "pointer": pointer}
                for path, digest, pointer in sorted(self.refs)]

    def call(self, operation, *args):
        try:
            return operation(*args)
        except _EvidenceSnapshotError as error:
            if error.code == "MISSING_FILE":
                raise _ProofIssue("MISSING_ARTIFACT") from None
            if error.code in {"UNSUPPORTED_SOURCE", "UNSUPPORTED_COMMIT"}:
                raise _ProofIssue("UNSUPPORTED_SCHEMA") from None
            raise

    def read(self, kind, key=None):
        body = self.call(self.snapshot._read, kind, key)
        path, _ = _artifact(kind, key)
        self.reference(path, _sha(body))
        return body

    def value(self, kind, key=None):
        return _json(self.read(kind, key), self.tick)

    def blob(self, commit, path):
        value = self.call(self.snapshot._git_blob, _hex(commit, True), _path(path))
        _need(value is not None, "SOURCE_MISMATCH")
        self.reference(path, value["sha256"])
        return value

    def pointer(self, document, pointer, path, digest):
        self.traversals += 1
        if self.traversals > 256:
            raise _EvidenceFilesError("COUNT_LIMIT") from None
        self.charge("reference", path + "#" + pointer)
        _need(pointer == "" or pointer.startswith("/"), "REFERENCE_CONFLICT")
        value = document
        tokens = pointer.split("/")[1:] if pointer else []
        _need(len(tokens) <= 32, "REFERENCE_CONFLICT")
        for token in tokens:
            self.tick()
            _need(re.search(r"~(?![01])", token) is None, "REFERENCE_CONFLICT")
            token = token.replace("~1", "/").replace("~0", "~")
            if type(value) is list:
                _need(re.fullmatch(r"0|[1-9][0-9]*", token) and len(token) <= 6,
                      "REFERENCE_CONFLICT")
                index = int(token)
                _need(index < len(value), "REFERENCE_CONFLICT")
                value = value[index]
            else:
                _need(type(value) is dict and token in value, "REFERENCE_CONFLICT")
                value = value[token]
        self.reference(path, digest, pointer)
        return value

    def document(self, path, digest, commit, current=False):
        _receipt_path(path)
        _hex(digest)
        _hex(commit, True)
        self.snapshot._files._observe(path, path[len(_INVENTORY):])
        _need(commit == self.head or self.call(self.snapshot._is_ancestor, commit, self.head), "REFERENCE_CONFLICT")
        blob = self.blob(commit, path)
        _need(blob["sha256"] == digest, "HASH_MISMATCH")
        if current:
            _need(self.read("receipt", path[len(_INVENTORY):]) == blob["data"], "HASH_MISMATCH")
        key = commit, path, digest
        if key not in self.cache:
            value = _json(blob["data"], self.tick)
            _need(type(value) is dict and value.get("schema_version") == 1 and
                  type(value.get("schema_version")) is int, "UNSUPPORTED_SCHEMA")
            self.cache[key] = value
        self.reference(path, digest)
        return self.cache[key]

    def index(self):
        body = self.read("ledger")
        _need(self.blob(self.head, _LEDGER)["data"] == body, "HASH_MISMATCH")
        self.ledger_sha = _sha(body)
        try:
            lines = body.decode("utf-8", errors="strict").splitlines(keepends=True)
            positions = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == _HEADING]
            _need(len(positions) == 1)
            start = positions[0] + 1
            _need(lines[start].rstrip("\r\n") == "```json")
            end = start + 1
            while end < len(lines) and lines[end].rstrip("\r\n") != "```":
                self.tick()
                end += 1
            _need(end < len(lines))
            raw = "".join(lines[start + 1:end]).encode("utf-8")
            index = _json(raw, self.tick)
            _need(type(index) is dict and set(index) == {"common_receipt_pointers", "field_order", "rows"})
            _need(index["common_receipt_pointers"] == ["/source", "/actor", "/scope", "/git"] and
                  index["field_order"] == list(_FIELDS))
            rows = index["rows"]
            _need(type(rows) is list and len(rows) <= 64)
            seen, result = set(), []
            for ordinal, values in enumerate(rows):
                _need(type(values) is list and len(values) == len(_FIELDS) and
                      all(type(item) is str and item for item in values))
                _need(tuple(values) not in seen)
                seen.add(tuple(values))
                row = dict(zip(_FIELDS, values))
                _hex(row["source_commit"], True)
                for field in ("source_file_sha256", "receipt_sha256", "evidence_limits_sha256"):
                    _hex(row[field])
                _receipt_path(row["receipt_path"])
                _receipt_path(row["evidence_limits_path"])
                _path(row["canonical_symbol"].split(":")[0])
                _need(row["canonical_symbol"].count(":") == 1 and
                      row["canonical_symbol"].split(":")[1].isidentifier())
                _need(len(row["scenario_key"].encode("utf-8")) <= 128 and
                      "::" in row["selector"])
                _identifier(row["seed_id"])
                _identifier(row["scenario_key"])
                _path(row["selector"].split("::")[0])
                row["index_ordinal"] = ordinal
                result.append(row)
            self.index_sha = _sha(raw)
            return result
        except (IndexError, UnicodeError):
            raise _ProofIssue("MALFORMED_EVIDENCE") from None

    def anchor(self, value):
        _need(type(value) is dict, "REFERENCE_CONFLICT")
        path = _receipt_path(value.get("path"))
        digest = _hex(value.get("sha256"))
        commit = _pick([value], "commit", "accepted_commit")
        return path, digest, _hex(commit, True)

    def named_anchor(self, provenance, name):
        anchor = self.anchor(provenance.get(name))
        for key in ("registration", "accepted_login_actions_registration", "custody"):
            if key in provenance and key != name:
                other = self.anchor(provenance[key])
                _need(other[0] != anchor[0] or other == anchor, "REFERENCE_CONFLICT")
        return anchor

    def _walk(self, document, path, digest, commit, stack=()):
        key = path, digest, commit
        _need(key not in stack, "REFERENCE_CONFLICT")
        if len(stack) >= 8:
            raise _EvidenceFilesError("COUNT_LIMIT") from None
        stack = (*stack, key)
        result = [(path, digest, document)]
        provenance = document.get("provenance_references", {})
        _need(type(provenance) is dict)
        source, actor, scope, git = [document.get(name, {}) for name in ("source", "actor", "scope", "git")]
        _need(all(type(value) is dict or value is None for value in (source, actor, scope, git)))
        source, actor, scope, git = [value or {} for value in (source, actor, scope, git)]
        targets = {}
        for section, field, expected in ((source, "registration_source_pointer", "/source"),
                (actor, "registration_actor_pointer", "/actor"),
                (scope, "registration_scope_pointer", "/scope"),
                (git, "registration_git_pointer", "/git")):
            if field in section:
                _need(section[field] == expected, "REFERENCE_CONFLICT")
                anchor = self.named_anchor(provenance, "registration")
                targets[anchor] = True
                target = self.document(*anchor)
                self.pointer(target, expected, anchor[0], anchor[1])
        fields = ((actor, "registration_actor_ref", "/actor"),
                  (actor, "worker_conversation_ref", "/actor/worker"),
                  (actor, "reviewer_conversation_ref", "/actor/reviewer"),
                  (actor, "source_proposal_ref", "/source/source_proposal_sha256"),
                  (source, "registration_source_manifest_ref", "/source/source_manifest_sha256"),
                  (scope, "source_assignment_ref", "/scope/source_assignment"),
                  (scope, "registration_assignment_ref", "/scope/registration_assignment"))
        for section, field, expected in fields:
            if field not in section:
                continue
            reference = section[field]
            _need(type(reference) is str and reference.count("#") == 1, "REFERENCE_CONFLICT")
            target_path, pointer = reference.split("#")
            _need(pointer == expected, "REFERENCE_CONFLICT")
            anchor = self.named_anchor(provenance, "accepted_login_actions_registration")
            _need(anchor[0] == target_path, "REFERENCE_CONFLICT")
            targets[anchor] = True
            target = self.document(*anchor)
            self.pointer(target, pointer, anchor[0], anchor[1])
        if "generation_custody" in source:
            anchor = self.anchor(source["generation_custody"])
            if "custody" in provenance:
                _need(self.anchor(provenance["custody"]) == anchor, "REFERENCE_CONFLICT")
            targets[anchor] = True
        for anchor in sorted(targets):
            target = self.document(*anchor)
            result.extend(self._walk(target, *anchor, stack))
        return result

    def attestation(self, row):
        self.refs = set()
        self.reference(_LEDGER, self.ledger_sha)
        path, digest = row["receipt_path"], row["receipt_sha256"]
        document = self.document(path, digest, self.head, current=True)
        _need(document.get("stage") == "P1-00b" and
              document.get("canonical_gap") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
              "ACCEPTANCE_MISMATCH")
        _need(document.get("status") == "PARTIAL_RECONCILIATION", "UNSUPPORTED_SCHEMA")
        documents = self._walk(document, path, digest, self.head)
        for pointer in ("/source", "/actor", "/scope", "/git"):
            _need(type(self.pointer(document, pointer, path, digest)) is dict)
        handler = self.pointer(document, row["handler_pointer"], path, digest)
        _need(type(handler) is dict)
        for field in ("seed_id", "canonical_symbol", "scenario_key", "selector", "source_commit", "source_file_sha256"):
            _need(handler.get(field) == row[field], "REFERENCE_CONFLICT")
        limit_path, limit_sha = row["evidence_limits_path"], row["evidence_limits_sha256"]
        if (limit_path, limit_sha) == (path, digest):
            limits_document = document
        else:
            anchor = self.named_anchor(document.get("provenance_references", {}), "registration")
            _need(anchor[:2] == (limit_path, limit_sha), "REFERENCE_CONFLICT")
            limits_document = self.document(*anchor)
        limits = self.pointer(limits_document, row["evidence_limits_pointer"], limit_path, limit_sha)
        _need(type(limits) in (dict, str) and bool(limits))
        sections = {name: [value[name] for _, _, value in documents if type(value.get(name)) is dict]
                    for name in ("source", "actor", "scope", "git")}
        generation_id = _pick(sections["actor"], "generation_assignment_id", required=False)
        generation_sha = _pick(sections["actor"], "generation_assignment_sha256", required=False)
        _need((generation_id is None) == (generation_sha is None), "REFERENCE_CONFLICT")
        if generation_id is not None:
            _identifier(generation_id)
            _hex(generation_sha)
        for scope in sections["scope"]:
            for name in ("source_assignment", "registration_assignment"):
                if name not in scope:
                    continue
                assignment = scope[name]
                _need(type(assignment) is dict, "UNSUPPORTED_SCHEMA")
                _identifier(assignment.get("assignment_id"))
                digest = _hex(assignment.get("sha256" if name == "source_assignment" else "scope_digest"))
                if name == "source_assignment" and generation_id is not None:
                    _need((assignment["assignment_id"], digest) == (generation_id, generation_sha), "REFERENCE_CONFLICT")
                if "stage" in assignment:
                    _need(assignment["stage"] == "P1-00b", "REFERENCE_CONFLICT")
        return {"row": row, "receipt": document, "documents": documents, **sections}
