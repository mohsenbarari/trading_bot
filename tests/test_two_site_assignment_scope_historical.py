"""Codex-authored evidence for the descriptive Assignment-bytes boundary.

No historical custody, acceptance-time, final-ACK, exclusion or activation claim.
"""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import builtins
from contextlib import ExitStack, contextmanager
import dataclasses
import datetime as dt
import hashlib
import inspect
import io
import json
from pathlib import Path
import socket
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
import yaml
from scripts import two_site_assignment_scope as subject
from tests import test_two_site_assignment_scope_core as core
from tests.test_two_site_assignment_scope_core import valid_repo

ID, STAGE, BRANCH = "WA-PARSER-LAB-001", "P1-00b", "main"
FENCE = chr(96) * 3
DOC_REL = ".local/two-site-refactor/assignments/" + ID + ".md"
REG_REL = ".local/two-site-refactor/worktrees.yaml"
ORDER_REL = "docs/refactor/two-server/execution-order.yaml"
FUTURE = "2099-01-01T00:00:00Z"


def _document(changes=None, *, drop=(), label="yaml"):
    data = {
        "id": ID, "status": "READY", "role": "WORKER",
        "integration_branch": BRANCH, "external_access": "NONE",
        "expires_at": FUTURE, "stage_id": STAGE,
        "allowed_paths": ["scripts/alpha.py", "tests/[ab]*.py"],
        "forbidden_paths": ["tests/broken.py"],
    }
    data.update(changes or {})
    for key in drop:
        del data[key]
    return ("# Owned parser document\n\n" + FENCE + label + "\n"
            + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
            + FENCE + "\n# Outside the YAML\n").encode("utf-8")


def _golden(raw, *, expiry=FUTURE, allowed=None, forbidden=None, branch=BRANCH):
    # Independent explicit contract, never derived from a parsed Subject result.
    allowed = ["scripts/alpha.py", "tests/[ab]*.py"] if allowed is None else allowed
    forbidden = ["tests/broken.py"] if forbidden is None else forbidden
    contract = {
        "allowed_paths": allowed, "assignment_id": "WA-PARSER-LAB-001",
        "expires_at": expiry, "external_access": "NONE",
        "forbidden_paths": forbidden, "integration_branch": branch,
        "role": "WORKER", "stage_id": "P1-00b", "status": "READY",
    }
    digest = hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n" + raw
    ).hexdigest()
    return {
        "assignment_id": "WA-PARSER-LAB-001", "stage_id": "P1-00b",
        "allowed_paths": tuple(allowed), "forbidden_paths": tuple(forbidden),
        "expires_at": expiry, "sha256": digest,
    }


def _parse(raw, **kwargs):
    return subject.parse_assignment_scope_bytes(
        raw, expected_assignment_id=kwargs.get("expected_assignment_id", ID),
        integration_branch=kwargs.get("integration_branch", BRANCH),
    )


def _reject(call, message, code="ASSIGNMENT_CONTRACT_INVALID"):
    with pytest.raises(subject.AssignmentScopeError) as caught:
        call()
    error = caught.value
    assert str(error) == message
    assert error.args == (message,)
    assert type(error.fact) is subject.AssignmentFact
    assert (error.fact.code, error.fact.path) == (code, None)
    assert (error.code, error.path) == (code, None)
    return error


def _clock(instant=None, *, deny=False):
    real = dt.datetime

    class ClockMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, real)

    class Clock(real, metaclass=ClockMeta):
        @classmethod
        def now(cls, timezone=None):
            if deny:
                raise AssertionError("parser read NOW")
            assert timezone is dt.timezone.utc
            return instant

        @classmethod
        def utcnow(cls):
            raise AssertionError("parser read UTCNOW")

        @classmethod
        def today(cls):
            raise AssertionError("parser read TODAY")

    return SimpleNamespace(datetime=Clock, date=dt.date, timezone=dt.timezone)


@contextmanager
def _no_io():
    calls, originals = [], []

    def blocked(name):
        def deny(*args, **kwargs):
            calls.append(name)
            raise AssertionError("descriptive parser crossed " + name)
        return deny

    groups = [
        (builtins, ("open",)), (io, ("open",)),
        (os, ("open", "read", "write", "stat", "lstat", "fstat", "listdir",
              "scandir", "readlink", "system", "popen")),
        (subprocess, ("Popen", "run", "check_output", "check_call", "call")),
        (socket, ("socket", "create_connection", "getaddrinfo")),
        (time, ("time", "time_ns", "monotonic", "monotonic_ns", "perf_counter")),
        (subject, ("_run_git_cmd", "_safe_read_file_bytes", "_resolve_shared_checkout",
                   "_get_current_branch", "_check_no_symlinks",
                   "_validate_stage_in_execution_order", "load_active_assignment_scope")),
    ]
    try:
        with ExitStack() as stack:
            for owner, names in groups:
                for name in names:
                    original = getattr(owner, name)
                    originals.append((owner, name, original))
                    stack.enter_context(patch.object(owner, name, blocked(name)))
            stack.enter_context(patch.object(subject, "datetime", _clock(deny=True)))
            yield calls
            assert calls == []
    finally:
        for owner, name, original in originals:
            assert getattr(owner, name) is original


def _git(root, *args):
    assert root.resolve() == root and root.is_relative_to(Path("/tmp"))
    result = subprocess.run(
        ["/usr/bin/git", "--no-replace-objects", "-C", str(root), *args],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
        capture_output=True, check=True,
    )
    return result.stdout.decode().strip()


@pytest.fixture
def repo(valid_repo):
    # Reuse the supplied real Git/registry/tracked-stage fixture, not its oracle.
    core._write_worktrees(valid_repo, active_assignment_id=ID)
    (valid_repo / DOC_REL).write_bytes(_document())
    return valid_repo


def _snapshot(root):
    result = {}
    for name in (ORDER_REL, REG_REL, DOC_REL):
        path = root / name
        info = path.stat()
        result[name] = (path.read_bytes(), info.st_dev, info.st_ino,
                        info.st_size, info.st_mtime_ns, info.st_nlink)
    return (_git(root, "rev-parse", "HEAD"),
            _git(root, "status", "--porcelain=v1", "--untracked-files=all"), result)


def _unchanged(root, call):
    before = _snapshot(root)
    try:
        return call()
    finally:
        assert _snapshot(root) == before


def test_real_git_registry_digest_and_unchanged_public_apis(repo):
    raw = (repo / DOC_REL).read_bytes()
    expected = _golden(raw)
    actual = _unchanged(repo, lambda: subject.load_active_assignment_scope(repo))
    parsed = _parse(raw)
    assert type(actual) is type(parsed) is subject.AssignmentScope
    assert dataclasses.asdict(actual) == dataclasses.asdict(parsed) == expected
    assert _unchanged(repo, lambda: subject.load_expected_assignment_scope(
        repo, ID, expected["sha256"])) == actual
    assert subject.validate_task_scope(actual, {
        "goal": "Owned " + ID + " evidence", "allowed_files": ["scripts/alpha.py"],
    }) is None
    assert subject.validate_changed_paths(actual, ["scripts/alpha.py"]) is None
    _reject(lambda: _unchanged(repo, lambda: subject.load_expected_assignment_scope(
        repo, ID, "0" * 64)), "Active assignment sha256 digest does not match expected digest")
    _reject(lambda: _unchanged(repo, lambda: subject.load_expected_assignment_scope(
        repo, "OTHER-ID", expected["sha256"])), "Active assignment ID does not match expected assignment ID")


@pytest.mark.parametrize("changes,drop,expiry", [
    ({}, (), FUTURE),
    ({"expires_at": "2001-02-03T04:05:06Z"}, (), "2001-02-03T04:05:06Z"),
    ({"expires_at": "2099-01-01T02:30:00+02:30"}, (), FUTURE),
    ({"expires_at": "2098-12-31T19:00:00-05:00"}, (), FUTURE),
    ({"expires_at": dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)}, (), FUTURE),
    ({"expires_at": "2099-01-01T00:00:00.123456+00:00"}, (), "2099-01-01T00:00:00.123456Z"),
    ({"expires_at": "0001-01-01T01:00:00+01:00"}, (), "0001-01-01T00:00:00Z"),
    ({"expires_at": "9999-12-31T22:59:59-01:00"}, (), "9999-12-31T23:59:59Z"),
    ({"stages": [STAGE]}, ("stage_id",), FUTURE),
    ({"stages": [STAGE]}, (), FUTURE),
    ({"assignment_id": ID}, ("id",), FUTURE),
    ({"id": "", "assignment_id": ID}, (), FUTURE),
    ({"assignment_id": "IGNORED-ALIAS", "unknown_field": {"kept": [1, 2]}}, (), FUTURE),
])
def test_descriptive_normalization_accepted_forms_and_independent_digest(changes, drop, expiry):
    raw = _document(changes, drop=drop)
    with _no_io():
        actual = _parse(raw)
        assert dataclasses.asdict(actual) == _golden(raw, expiry=expiry)
    assert set(dataclasses.asdict(actual)) == {
        "assignment_id", "stage_id", "allowed_paths", "forbidden_paths", "expires_at", "sha256",
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        actual.expires_at = "1900-01-01T00:00:00Z"


def test_ordered_paths_raw_prose_comments_whitespace_and_offset_digest():
    allowed = ["tests/z.py", "scripts/a.py", "tests/[ab]*.py", "docs/نمونه.md"]
    forbidden = ["tests/zbad.py", "scripts/private/*"]
    base = _document({"allowed_paths": allowed, "forbidden_paths": forbidden})
    variants = [
        base, b"# Before\n" + base, base + b"\n# After\n",
        base.replace(b"# Outside the YAML", b"# Outside  the YAML"),
        base.replace(b"\n" + FENCE.encode() + b"\n", b"\n  " + FENCE.encode() + b" \n"),
    ]
    digests = []
    for raw in variants:
        with _no_io():
            actual = _parse(raw)
            assert dataclasses.asdict(actual) == _golden(raw, allowed=allowed, forbidden=forbidden)
        digests.append(actual.sha256)
    assert len(set(digests)) == len(variants)
    offset = _document({"expires_at": "2099-01-01T01:00:00+01:00"})
    plain = _document()
    assert dataclasses.asdict(_parse(offset)) == _golden(offset)
    assert dataclasses.asdict(_parse(plain)) == _golden(plain)
    assert _parse(offset).expires_at == _parse(plain).expires_at == FUTURE
    assert _parse(offset).sha256 != _parse(plain).sha256


def test_strict_builtin_bytes_without_hostile_hooks_and_real_size_boundaries():
    hooks = []

    class HostileBytes(bytes):
        def __len__(self):
            hooks.append("len")
            raise AssertionError("hostile len reached")

        def decode(self, *args, **kwargs):
            hooks.append("decode")
            raise AssertionError("hostile decode reached")

    class NotBytes:
        def __bytes__(self):
            hooks.append("bytes")
            raise AssertionError("conversion reached")

    for value in (None, "text", bytearray(b"text"), memoryview(b"text"), 12, NotBytes(), HostileBytes(_document())):
        with _no_io():
            _reject(lambda: _parse(value), "assignment_bytes must be exact bytes")
    assert hooks == []
    raw = _document()
    exact = raw + b"#" + b"x" * (524288 - len(raw) - 1)
    assert len(exact) == subject.MAX_CONTROL_FILE_BYTES == 524288
    with _no_io():
        actual = _parse(exact)
        assert dataclasses.asdict(actual) == _golden(exact)
        _reject(lambda: _parse(exact + b"x"), "Assignment bytes oversized")
        _reject(lambda: _parse(exact + b"\xff"), "Assignment bytes oversized")


@pytest.mark.parametrize("changes,kwargs,message", [
    ({"id": "WRONG"}, {}, "Assignment document id does not match active_assignment_id"),
    ({}, {"expected_assignment_id": ""}, "Assignment ID cannot be empty"),
    ({}, {"expected_assignment_id": " BAD"}, "Assignment ID has leading or trailing whitespace"),
    ({}, {"expected_assignment_id": "bad/id"}, "Invalid characters in Assignment ID"),
    ({}, {"expected_assignment_id": 3}, "Assignment ID must be a string"),
    ({}, {"integration_branch": ""}, "integration_branch must be a nonempty string"),
    ({}, {"integration_branch": None}, "integration_branch must be a nonempty string"),
    ({}, {"integration_branch": 3}, "integration_branch must be a nonempty string"),
    ({"integration_branch": "other"}, {}, "Assignment integration_branch does not match current branch"),
    ({"status": "DRAFT"}, {}, "Assignment status must be READY"),
    ({"role": "CONTRIBUTOR"}, {}, "Assignment role must be WORKER"),
    ({"external_access": "FULL"}, {}, "Assignment external_access must be NONE"),
])
def test_invalid_identity_and_header_fields(changes, kwargs, message):
    raw = _document(changes)
    with _no_io():
        _reject(lambda: _parse(raw, **kwargs), message)


def test_branch_parameter_has_no_invented_grammar():
    branch = "literal branch value"
    raw = _document({"integration_branch": branch})
    with _no_io():
        assert dataclasses.asdict(_parse(raw, integration_branch=branch)) == _golden(raw, branch=branch)


def test_yaml_utf8_and_fence_faults_fail_closed():
    raw = _document()
    cases = [
        (b"\xff", "Assignment file is not valid UTF-8"),
        (b"no fence", "Assignment Markdown must contain exactly one fenced YAML block"),
        (raw + "\n".encode() + _document(), "Assignment Markdown must contain exactly one fenced YAML block"),
        ((FENCE + "yaml\n[1, 2]\n" + FENCE + "\n").encode(), "Assignment YAML block must be a mapping"),
        ((FENCE + "yaml\nid: [\n" + FENCE + "\n").encode(), "Failed to parse YAML document"),
        (raw.replace(b"role: WORKER\n", b"role: WORKER\nrole: WORKER\n"), "Duplicate key in YAML mapping"),
        (raw.replace(b"role: WORKER\n", b"role: WORKER\nother: {same: 1, same: 2}\n"), "Duplicate key in YAML mapping"),
        ((FENCE + "yaml\n? [one, two]\n: value\n" + FENCE + "\n").encode(), "Unhashable key in YAML mapping"),
        (_document(label="python"), "Assignment Markdown must contain exactly one fenced YAML block"),
    ]
    for body, message in cases:
        with _no_io():
            _reject(lambda: _parse(body), message)
    for label in ("yaml", "yml", ""):
        body = _document(label=label)
        assert dataclasses.asdict(_parse(body)) == _golden(body)


@pytest.mark.parametrize("value,drop,message", [
    (None, ("expires_at",), "Assignment document missing 'expires_at'"),
    (None, (), "Assignment document missing 'expires_at'"),
    (dt.date(2099, 1, 1), (), "expires_at must include time and timezone information"),
    (dt.datetime(2099, 1, 1), (), "expires_at must include timezone information"),
    ("2099-01-01T00:00:00", (), "expires_at must include timezone information"),
    ("", (), "expires_at cannot be empty"),
    ("not-a-date", (), "Invalid expires_at format"),
    (12, (), "Unsupported expires_at type"),
])
def test_invalid_expiry_forms_have_typed_contract_errors(value, drop, message):
    raw = _document({"expires_at": value}, drop=drop)
    with _no_io():
        _reject(lambda: _parse(raw), message)


@pytest.mark.parametrize("expiry", [
    "0001-01-01T00:00:00+01:00",
    "9999-12-31T23:59:59-01:00",
])
def test_descriptive_utc_normalization_overflow_is_typed_without_io_or_now(expiry):
    raw = _document({"expires_at": expiry})
    with _no_io():
        error = _reject(lambda: _parse(raw), "expires_at UTC normalization out of range")
        assert type(error.__cause__) is OverflowError


@pytest.mark.parametrize("changes,drop,message", [
    ({}, ("stage_id",), "Assignment must specify exactly one stage_id"),
    ({"stages": STAGE}, (), "Assignment stages must be a list or tuple"),
    ({"stages": []}, (), "Assignment stages must have exactly one element"),
    ({"stages": [STAGE, "other"]}, (), "Assignment stages must have exactly one element"),
    ({"stages": ["other"]}, (), "Mismatch between stage_id and stages[0]"),
    ({"stage_id": "bad/stage"}, (), "Invalid characters in Stage ID"),
    ({"allowed_paths": []}, (), "allowed_paths cannot be empty"),
    ({"forbidden_paths": []}, (), "forbidden_paths cannot be empty"),
    ({"allowed_paths": ["tests/a.py"] * 257}, (), "allowed_paths exceeds maximum pattern count"),
    ({"allowed_paths": ["tests/a.py", "tests/a.py"]}, (), "Duplicate pattern rejected in allowed_paths"),
    ({"allowed_paths": "tests/a.py"}, (), "allowed_paths must be a list or tuple"),
    ({"allowed_paths": [3]}, (), "Item in allowed_paths must be a string"),
    ({"allowed_paths": ["../escape"]}, (), "Dot-dot '..' component rejected"),
    ({"allowed_paths": ["/absolute"]}, (), "Absolute paths rejected"),
    ({"allowed_paths": ["tests/[z-a].py"]}, (), "Invalid character range in bracket pattern"),
    ({"allowed_paths": ["tests/[a.py"]}, (), "Unclosed bracket expression in pattern"),
    ({"allowed_paths": ["*"]}, (), "Bare '*' pattern rejected"),
    ({"forbidden_paths": ["tests/.git/file"]}, (), "'.git' component rejected"),
])
def test_stage_and_path_rules_are_the_existing_ordered_contract(changes, drop, message):
    raw = _document(changes, drop=drop)
    with _no_io():
        _reject(lambda: _parse(raw), message)


def test_active_expiry_equal_past_and_header_stage_path_precedence(repo):
    instant = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
    original = (repo / DOC_REL).read_bytes()
    with patch.object(subject, "datetime", _clock(instant)):
        try:
            for expiry in ("2029-12-31T23:59:59.999999Z", "2030-01-01T00:00:00Z"):
                for extra in ({}, {"stages": []}, {"allowed_paths": []},
                              {"stage_id": "bad/stage", "forbidden_paths": []}):
                    raw = _document({"expires_at": expiry, **extra})
                    (repo / DOC_REL).write_bytes(raw)
                    _reject(lambda: _unchanged(repo, lambda: subject.load_active_assignment_scope(repo)),
                            "Assignment has expired", "ASSIGNMENT_EXPIRED")
                    _reject(lambda: _unchanged(repo, lambda: subject.load_expected_assignment_scope(repo, ID, "0" * 64)),
                            "Assignment has expired", "ASSIGNMENT_EXPIRED")
            raw = _document({"expires_at": "2020-01-01T00:00:00Z", "status": "DRAFT", "stages": []})
            (repo / DOC_REL).write_bytes(raw)
            _reject(lambda: _unchanged(repo, lambda: subject.load_active_assignment_scope(repo)),
                    "Assignment status must be READY")
            raw = _document({"expires_at": "2030-01-01T00:00:00.000001Z"})
            (repo / DOC_REL).write_bytes(raw)
            actual = _unchanged(repo, lambda: subject.load_active_assignment_scope(repo))
            assert dataclasses.asdict(actual) == _golden(raw, expiry="2030-01-01T00:00:00.000001Z")
        finally:
            (repo / DOC_REL).write_bytes(original)
    assert (repo / DOC_REL).read_bytes() == original


def test_active_tracked_stage_is_still_required(repo):
    path = repo / ORDER_REL
    original = path.read_bytes()
    try:
        path.write_text("stages:\n  P1-00b:\n    state: PENDING\n")
        _reject(lambda: _unchanged(repo, lambda: subject.load_active_assignment_scope(repo)),
                "Stage 'P1-00b' status in execution-order is 'PENDING', expected 'IN_PROGRESS'")
    finally:
        path.write_bytes(original)
    assert path.read_bytes() == original


def test_public_export_signature_and_shared_raw_primitives(repo):
    signature = inspect.signature(subject.parse_assignment_scope_bytes)
    assert list(signature.parameters) == ["assignment_bytes", "expected_assignment_id", "integration_branch"]
    assert [p.kind for p in signature.parameters.values()] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.KEYWORD_ONLY,
    ]
    assert all(p.default is inspect.Parameter.empty for p in signature.parameters.values())
    assert "parse_assignment_scope_bytes" in subject.__all__
    raw = (repo / DOC_REL).read_bytes()
    with patch.object(subject, "_parse_assignment_header", wraps=subject._parse_assignment_header) as header:
        with patch.object(subject, "_parse_assignment_stage_paths", wraps=subject._parse_assignment_stage_paths) as paths:
            with patch.object(subject, "_finalize_assignment_scope", wraps=subject._finalize_assignment_scope) as final:
                described = _parse(raw)
                active = _unchanged(repo, lambda: subject.load_active_assignment_scope(repo))
                assert dataclasses.asdict(described) == dataclasses.asdict(active) == _golden(raw)
                assert header.call_args_list == [call(raw, ID, BRANCH), call(raw, ID, BRANCH)]
                assert header.call_count == paths.call_count == final.call_count == 2
    assert dataclasses.asdict(_parse(raw)) == _golden(raw)
