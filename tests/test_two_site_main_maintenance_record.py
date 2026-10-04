"""Independent full v1.1 graph/wire/custody oracles; v1 remains unchanged."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

for _path, _cap in ((Path(__file__).resolve(), 24000),
                   (Path(__file__).resolve().parents[1] / "scripts/two_site_main_maintenance_record.py", 12000)):
    with _path.open("rb") as _stream:
        _raw = _stream.read(_cap + 1)
    assert len(_raw) == _path.stat().st_size <= _cap

from scripts import two_site_main_maintenance_record as subject
from scripts import two_site_maintenance_transition_record as legacy
from scripts.two_site_main_transition_sequence import validate_main_transition_sequence
from scripts.two_site_protected_main_approval_record import parse_protected_main_approval_record

ERROR = "invalid main maintenance transition record"
MAIN, ORIGIN, CANDIDATE = "refs/heads/main", "refs/remotes/origin/main", "refs/heads/candidate/wa-ir-standby-v1"
CONTROL = "scripts/two_site_codex_autopilot.py"
RUNNER = "scripts/two_site_proposal_runner.py"


def wire(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def repository(head, assignment):
    return {"head": head, "status": "", "protected_refs": {MAIN: "2" * 40, ORIGIN: "3" * 40, CANDIDATE: "4" * 40},
        "control_blobs": {CONTROL: "5" * 40, RUNNER: "6" * 40},
        "assignment_scope": {"assignment_id": assignment, "stage_id": "P1-00b", "sha256": "7" * 64,
                             "expires_at": "2026-10-30T23:59:00Z", "allowed_paths": ["scripts/two_site_worker.py"], "forbidden_paths": []}}


def commit(head, parent, index):
    return {"head": head, "parent": parent, "paths": ["scripts/two_site_worker.py"],
        "worker_run_id": f"worker-{index}", "reviewer_run_id": f"reviewer-{index}",
        "proposal_sha256": "8" * 64, "test_evidence_sha256": "9" * 64}


def fixtures(pre_count=15, post_count=3):
    prior = repository("0" * 40, "assignment-old")
    pre, post, current = [], [], prior["head"]
    for index in range(1, pre_count + 1):
        head = f"{index:040x}"
        pre.append(commit(head, current, index))
        current = head
    main = {"head": "a" * 40, "parents": [current, "f" * 40], "paths": ["core/new_behavior.py"],
        "worker_run_id": "worker-main", "reviewer_run_id": "reviewer-main",
        "proposal_sha256": "b" * 64, "test_evidence_sha256": "c" * 64}
    current = main["head"]
    for index in range(post_count):
        head = f"{100 + index:040x}"
        post.append(commit(head, current, 100 + index))
        current = head
    new = repository(current, "assignment-new")
    new["protected_refs"][MAIN] = "f" * 40
    decision = {"schema_version": "1.0", "kind": "OWNER_APPROVED_PROTECTED_MAIN_CHANGE", "reference": MAIN,
        "prior_main_sha": "2" * 40, "approved_main_sha": "f" * 40, "prior_checkpoint_sha256": "d" * 64,
        "prior_head": prior["head"], "decision_id": "owner-explicit-main-decision"}
    owner = {"decision": decision, "raw_sha256": hashlib.sha256(wire(decision)).hexdigest()}
    record = {"schema_version": "1.1", "kind": "OWNER_APPROVED_MAIN_MAINTENANCE_TRANSITION",
        "prior_checkpoint_sha256": "d" * 64, "prior_head": prior["head"], "new_repository": new,
        "owner_approval": copy.deepcopy(owner), "pre_main_commits": pre, "main_integration": main, "post_main_commits": post}
    bindings = {"prior_checkpoint_sha256": "d" * 64, "prior_repository": prior, "owner_approval": owner}
    return record, bindings


def good(record, bindings):
    frozen = copy.deepcopy([record, bindings])
    result = subject.validate_main_maintenance_record(record, **bindings)
    assert result == record
    assert subject.serialize_main_maintenance_record(record, **bindings) == wire(record)
    assert subject.parse_main_maintenance_record(wire(record), **bindings) == record
    assert [record, bindings] == frozen
    return result


def bad(record, bindings):
    baseline, context = fixtures()
    good(baseline, context)
    frozen = copy.deepcopy([record, bindings])
    for action in (subject.validate_main_maintenance_record, subject.serialize_main_maintenance_record):
        with pytest.raises(subject.MainMaintenanceRecordError) as caught:
            action(record, **bindings)
        assert str(caught.value) == ERROR
        assert caught.value.__cause__ is None and caught.value.__suppress_context__ is True
    assert [record, bindings] == frozen
    good(baseline, context)


@pytest.mark.parametrize("pre,post", [(0, 0), (15, 3), (29, 2), (0, 31), (31, 0)])
def test_complete_graph_build_validate_wire_and_detachment(pre, post):
    record, bindings = fixtures(pre, post)
    result = good(record, bindings)
    args = {**bindings, **{k:record[k] for k in ("new_repository", "pre_main_commits", "main_integration", "post_main_commits")}}
    assert subject.build_main_maintenance_record(**args) == record
    result["owner_approval"]["decision"]["decision_id"] = "mutation"
    result["new_repository"]["assignment_scope"]["allowed_paths"].append("mutated")
    result["main_integration"]["parents"].reverse()
    good(record, bindings)


def test_actual_lower_helpers_exact_full_forwarding():
    record, bindings = fixtures()
    with (patch.object(subject, "_validate_new_repository", wraps=legacy._validate_new_repository) as repo,
          patch.object(subject, "parse_protected_main_approval_record", wraps=parse_protected_main_approval_record) as owner,
          patch.object(subject, "validate_main_transition_sequence", wraps=validate_main_transition_sequence) as sequence):
        assert subject.validate_main_maintenance_record(record, **bindings) == record
        assert repo.call_args_list == [((bindings["prior_repository"],),), ((record["new_repository"],),)]
        owner.assert_called_once_with(wire(bindings["owner_approval"]["decision"]),
            prior_main_sha="2" * 40, approved_main_sha="f" * 40, prior_checkpoint_sha256="d" * 64, prior_head="0" * 40)
        sequence.assert_called_once_with(record["pre_main_commits"], record["main_integration"], record["post_main_commits"],
            prior_head="0" * 40, new_head=record["new_repository"]["head"], approved_main_sha="f" * 40,
            control_blob_paths=frozenset({CONTROL, RUNNER}))


@pytest.mark.parametrize("key,value", [("schema_version", "1.0"), ("schema_version", 1.1),
    ("kind", "REVIEWED_MAINTENANCE_TRANSITION"), ("prior_checkpoint_sha256", "e" * 64),
    ("prior_checkpoint_sha256", True), ("prior_head", "1" * 40), ("prior_head", 0),
    ("pre_main_commits", ()), ("post_main_commits", {}), ("main_integration", []), ("owner_approval", None)])
def test_record_faults(key, value):
    record, bindings = fixtures()
    record[key] = value
    bad(record, bindings)


@pytest.mark.parametrize("key", sorted(subject.KEYS))
def test_every_missing_key_rejected(key):
    record, bindings = fixtures()
    del record[key]
    bad(record, bindings)


def test_unknown_key_and_nonobject():
    record, bindings = fixtures()
    record["approved"] = True
    bad(record, bindings)
    for value in (None, [], (), True, 1):
        bad(value, bindings)


@pytest.mark.parametrize("field,value", [(MAIN, "2" * 40), (ORIGIN, "1" * 40), (CANDIDATE, "1" * 40)])
def test_protected_reference_drift(field, value):
    record, bindings = fixtures()
    record["new_repository"]["protected_refs"][field] = value
    bad(record, bindings)


def test_repository_scope_and_endpoint_faults():
    for which, location, value in (
        ("new", "status", " M x.py"), ("prior", "status", "?? x.py"),
        ("new", "head", "0" * 40), ("new", "head", "e" * 40),
        ("new", "assignment_id", "assignment-old")):
        record, bindings = fixtures()
        repo = record["new_repository"] if which == "new" else bindings["prior_repository"]
        if location == "assignment_id": repo["assignment_scope"][location] = value
        else: repo[location] = value
        bad(record, bindings)
    for checkpoint in ("e" * 64, True, "D" * 64, "d" * 63, None):
        record, bindings = fixtures()
        bindings["prior_checkpoint_sha256"] = checkpoint
        bad(record, bindings)


def test_owner_whole_wrapper_and_bound_decision_faults():
    for field in ("prior_main_sha", "approved_main_sha", "prior_checkpoint_sha256", "prior_head", "reference", "decision_id"):
        record, bindings = fixtures()
        bindings["owner_approval"]["decision"][field] = "mismatch"
        bindings["owner_approval"]["raw_sha256"] = hashlib.sha256(wire(bindings["owner_approval"]["decision"])).hexdigest()
        bad(record, bindings)
    for mutate in (lambda o:o.update(raw_sha256="0" * 64), lambda o:o.update(extra=True),
                   lambda o:o["decision"].update(extra=True), lambda o:o["decision"].update(decision_id="x" * 129)):
        record, bindings = fixtures()
        mutate(bindings["owner_approval"])
        bad(record, bindings)
    record, bindings = fixtures()
    record["owner_approval"]["decision"]["decision_id"] = "different-valid-id"
    record["owner_approval"]["raw_sha256"] = hashlib.sha256(wire(record["owner_approval"]["decision"])).hexdigest()
    bad(record, bindings)


def test_control_change_requires_allowlist_and_declaration():
    record, bindings = fixtures()
    record["new_repository"]["control_blobs"][CONTROL] = "e" * 40
    bad(record, bindings)
    record["pre_main_commits"][0]["paths"] = sorted([CONTROL, "scripts/two_site_worker.py"])
    good(record, bindings)
    record["new_repository"]["control_blobs"][RUNNER] = "e" * 40
    bad(record, bindings)
    record, bindings = fixtures()
    del record["new_repository"]["control_blobs"][RUNNER]
    bad(record, bindings)


def test_full_graph_discontinuity_duplicates_and_limit():
    record, bindings = fixtures()
    record["pre_main_commits"][12]["parent"] = "0" * 40
    bad(record, bindings)
    record, bindings = fixtures()
    record["main_integration"]["parents"].reverse()
    bad(record, bindings)
    record, bindings = fixtures()
    record["post_main_commits"][0]["parent"] = "1" * 40
    bad(record, bindings)
    record, bindings = fixtures()
    record["post_main_commits"][1]["head"] = record["pre_main_commits"][0]["head"]
    record["post_main_commits"][2]["parent"] = record["pre_main_commits"][0]["head"]
    bad(record, bindings)
    record, bindings = fixtures(30, 2)
    bad(record, bindings)


@pytest.mark.parametrize("raw", [b"", b"x" * 65537, b"\xff", b"null", b"[]", b"NaN", b"Infinity",
    b'{"schema_version":"1.1","schema_version":"1.1"}', "not bytes", bytearray(b"{}")])
def test_bad_raw_wire(raw):
    record, bindings = fixtures()
    good(record, bindings)
    with pytest.raises(subject.MainMaintenanceRecordError, match=ERROR):
        subject.parse_main_maintenance_record(raw, **bindings)
    good(record, bindings)


def test_raw_byte_gate_before_json_and_canonical_spelling():
    record, bindings = fixtures()
    with patch.object(subject, "unique_json", side_effect=AssertionError("must not decode")) as decode:
        with pytest.raises(subject.MainMaintenanceRecordError):
            subject.parse_main_maintenance_record(b"x" * 65537, **bindings)
        decode.assert_not_called()
    for raw in (wire(record)[:-1], wire(record) + b"\n", json.dumps(record).encode(), b" " + wire(record)):
        with pytest.raises(subject.MainMaintenanceRecordError):
            subject.parse_main_maintenance_record(raw, **bindings)
    good(record, bindings)


def test_wire_limit_inclusive_and_streaming_rejection():
    record, bindings = fixtures()
    raw = wire(record)
    with patch.object(subject, "MAX_MAIN_MAINTENANCE_RECORD_BYTES", len(raw)):
        assert subject.serialize_main_maintenance_record(record, **bindings) == raw
        assert subject.parse_main_maintenance_record(raw, **bindings) == record
    with patch.object(subject, "MAX_MAIN_MAINTENANCE_RECORD_BYTES", len(raw) - 1):
        with pytest.raises(subject.MainMaintenanceRecordError):
            subject.serialize_main_maintenance_record(record, **bindings)
    # Validate stream termination without constructing the entire encoded output.
    seen = []
    def chunks(_value):
        for value in ("abcd", "efgh", "must-not-consume"):
            seen.append(value)
            yield value
    with patch.object(subject.json.JSONEncoder, "iterencode", side_effect=chunks):
        with pytest.raises(ValueError): subject._canonical({}, 8)
    assert seen == ["abcd", "efgh"]
    good(record, bindings)


@pytest.mark.parametrize("helper", ["_validate_new_repository", "parse_protected_main_approval_record", "validate_main_transition_sequence"])
def test_lower_error_hidden_with_prior_caller_context(helper):
    record, bindings = fixtures()
    good(record, bindings)
    try: raise RuntimeError("prior caller marker")
    except RuntimeError:
        with patch.object(subject, helper, side_effect=RuntimeError("lower marker")):
            with pytest.raises(subject.MainMaintenanceRecordError) as caught:
                subject.validate_main_maintenance_record(record, **bindings)
            assert str(caught.value) == ERROR and caught.value.__suppress_context__ is True
            assert caught.value.__cause__ is None
    good(record, bindings)


class Interrupt(BaseException): pass


class StringSubclass(str): pass
class DictSubclass(dict): pass
class ListSubclass(list): pass


@pytest.mark.parametrize("location", ["record", "record-key", "schema", "checkpoint", "prior", "new", "status", "owner", "decision", "decision-key", "record-owner-key", "record-decision-key", "pre"])
def test_plain_schema_types_and_keys(location):
    record, bindings = fixtures()
    if location == "record": record = DictSubclass(record)
    elif location == "record-key": record[StringSubclass("schema_version")] = record.pop("schema_version")
    elif location == "schema": record["schema_version"] = StringSubclass("1.1")
    elif location == "checkpoint": bindings["prior_checkpoint_sha256"] = StringSubclass("d" * 64)
    elif location == "prior": bindings["prior_repository"] = DictSubclass(bindings["prior_repository"])
    elif location == "new": record["new_repository"] = DictSubclass(record["new_repository"])
    elif location == "status": record["new_repository"]["status"] = StringSubclass("")
    elif location == "owner": bindings["owner_approval"] = DictSubclass(bindings["owner_approval"])
    elif location == "decision": bindings["owner_approval"]["decision"] = DictSubclass(bindings["owner_approval"]["decision"])
    elif location == "decision-key":
        d = bindings["owner_approval"]["decision"]
        d[StringSubclass("decision_id")] = d.pop("decision_id")
    elif location == "record-owner-key":
        d = record["owner_approval"]
        d[StringSubclass("raw_sha256")] = d.pop("raw_sha256")
    elif location == "record-decision-key":
        d = record["owner_approval"]["decision"]
        d[StringSubclass("decision_id")] = d.pop("decision_id")
    else: record["pre_main_commits"] = ListSubclass(record["pre_main_commits"])
    bad(record, bindings)


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit, Interrupt])
@pytest.mark.parametrize("helper", ["_validate_new_repository", "parse_protected_main_approval_record", "validate_main_transition_sequence", "unique_json"])
def test_base_exception_identity_unchanged(kind, helper):
    record, bindings = fixtures()
    good(record, bindings)
    marker = kind("original sentinel")
    with patch.object(subject, helper, side_effect=marker):
        with pytest.raises(kind) as caught:
            if helper == "unique_json": subject.parse_main_maintenance_record(wire(record), **bindings)
            else: subject.validate_main_maintenance_record(record, **bindings)
        assert caught.value is marker
    good(record, bindings)


def test_legacy_caps_and_real_v1_roundtrip_unchanged():
    assert legacy.MAX_MAINTENANCE_COMMITS == 12
    assert legacy.MAX_MAINTENANCE_TRANSITION_RECORD_BYTES == 16384
    record, bindings = fixtures(12, 0)
    repo = copy.deepcopy(bindings["prior_repository"])
    repo["head"] = record["pre_main_commits"][-1]["head"]
    old = legacy.build_maintenance_transition_record("d" * 64, "0" * 40, repo, record["pre_main_commits"])
    assert legacy.parse_maintenance_transition_record(legacy.serialize_maintenance_transition_record(old)) == old
    record, bindings = fixtures(13, 0)
    repo["head"] = record["pre_main_commits"][-1]["head"]
    with pytest.raises(legacy.MaintenanceTransitionRecordError):
        legacy.build_maintenance_transition_record("d" * 64, "0" * 40, repo, record["pre_main_commits"])
