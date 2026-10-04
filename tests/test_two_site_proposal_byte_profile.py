"""Independent grammar, custody and fail-closed tests for the optional profile."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

for _path, _cap in ((Path(__file__).resolve(), 24000),
                   (Path(__file__).resolve().parents[1] / "scripts/two_site_proposal_byte_profile.py", 8192)):
    _size = _path.stat().st_size
    with _path.open("rb") as _stream:
        _data = _stream.read(_cap + 1)
    assert len(_data) == _size <= _cap

from scripts import two_site_proposal_byte_profile as subject
from scripts.two_site_agent_sandbox import _validate_overlay_path

PREFIX = "PROPOSAL_UTF8_BUDGET_V1:"
ERROR = "invalid proposal UTF-8 budget profile"
ALLOWED = ["scripts/source.py", "tests/test_profile.py"]
ORACLE = {"version": "1.0", "file_limits": {"scripts/source.py": 12000, "tests/test_profile.py": 24000}, "total_limit": 36000}


def declaration(profile=ORACLE):
    return PREFIX + json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def good():
    return [["ordinary requirement", declaration()], copy.deepcopy(ALLOWED), copy.deepcopy(ALLOWED[:1])]


class ListSubclass(list):
    pass


class StringSubclass(str):
    def startswith(self, *args):
        raise AssertionError("must use builtin prefix detection")


class Interrupt(BaseException):
    pass


def assert_good(args):
    frozen = copy.deepcopy(args)
    oracle = copy.deepcopy(ORACLE)
    result = subject.parse_proposal_utf8_budget_profile(*args)
    assert result == oracle
    assert args == frozen and ORACLE == oracle
    return result


def assert_bad(args):
    baseline = good()
    frozen = copy.deepcopy(args)
    assert_good(baseline)
    with pytest.raises(subject.ProposalByteProfileError) as caught:
        subject.parse_proposal_utf8_budget_profile(*args)
    assert str(caught.value) == ERROR
    assert caught.value.__cause__ is None and caught.value.__suppress_context__ is True
    assert args == frozen
    assert_good(baseline)


def test_real_helpers_exact_forwarding_and_detached_result():
    args = good()
    with (patch.object(subject, "_unique_json", wraps=subject._unique_json) as decoder,
          patch.object(subject, "_validate_overlay_path", wraps=_validate_overlay_path) as paths):
        result = assert_good(args)
        decoder.assert_called_once_with(declaration()[len(PREFIX):])
        assert paths.call_args_list == [((p,),) for p in ALLOWED]
    result["file_limits"][ALLOWED[0]] = 1
    assert_good(args)


@pytest.mark.parametrize("requirements", [None, 7, {}, ["plain", 7, {"nested": PREFIX}],
    "plain", ("plain",), ListSubclass(["plain"]), ["mentions " + PREFIX], [" " + declaration()]])
def test_no_declaration_does_not_touch_other_arguments(requirements):
    forbidden = object()
    with (patch.object(subject, "_unique_json", side_effect=AssertionError("unexpected decode")),
          patch.object(subject, "_validate_overlay_path", side_effect=AssertionError("unexpected path"))):
        assert subject.parse_proposal_utf8_budget_profile(requirements, forbidden, forbidden) is None


@pytest.mark.parametrize("requirements", [declaration(), (declaration(),),
    ListSubclass([declaration()]), [StringSubclass(declaration())], [declaration(), declaration()]])
def test_reserved_declaration_requires_one_plain_list_string(requirements):
    args = good()
    args[0] = requirements
    assert_bad(args)


@pytest.mark.parametrize("body", ["x" * 8193, "é", "{} ", "{}\n",
    '{"version":"1.0","version":"1.0"}', "[]", "null", "true", "NaN", "Infinity",
    '{"version":"1.0","file_limits":{},"total_limit":1}'])
def test_bad_json_and_noncanonical_body(body):
    args = good()
    args[0][1] = PREFIX + body
    assert_bad(args)


def test_oversize_and_nonascii_rejected_before_decode():
    for body in ("x" * 8193, "é", " " * 8193):
        with patch.object(subject, "_unique_json", side_effect=AssertionError("must not decode")) as decode:
            with pytest.raises(subject.ProposalByteProfileError, match=ERROR):
                subject.parse_proposal_utf8_budget_profile([PREFIX + body], ALLOWED, ALLOWED)
            decode.assert_not_called()


def test_oversize_path_rejected_before_path_validator():
    args = good()
    args[1][0] = "x" * 100000
    with patch.object(subject, "_validate_overlay_path") as validator:
        with pytest.raises(subject.ProposalByteProfileError, match=ERROR):
            subject.parse_proposal_utf8_budget_profile(*args)
        validator.assert_not_called()


@pytest.mark.parametrize("key,value", [
    ("version", 1), ("version", "1.1"), ("version", True),
    ("total_limit", True), ("total_limit", 0), ("total_limit", -1),
    ("total_limit", 1.0), ("total_limit", "1"), ("total_limit", 160001),
    ("file_limits", []), ("file_limits", {}), ("file_limits", {ALLOWED[0]: 1}),
    ("file_limits", {**ORACLE["file_limits"], "scripts/extra.py": 1}),
])
def test_exact_profile_schema_and_types(key, value):
    profile = copy.deepcopy(ORACLE)
    profile[key] = value
    args = good()
    args[0][1] = declaration(profile)
    assert_bad(args)


@pytest.mark.parametrize("cap", [True, False, 0, -1, 160001, 1.0, "1", None])
def test_file_limit_plain_integer(cap):
    profile = copy.deepcopy(ORACLE)
    profile["file_limits"][ALLOWED[0]] = cap
    args = good()
    args[0][1] = declaration(profile)
    assert_bad(args)


def test_missing_extra_schema_and_noncanonical_order():
    for key in ORACLE:
        profile = copy.deepcopy(ORACLE)
        del profile[key]
        args = good()
        args[0][1] = declaration(profile)
        assert_bad(args)
    profile = {**ORACLE, "extra": 1}
    args = good()
    args[0][1] = declaration(profile)
    assert_bad(args)
    args[0][1] = PREFIX + json.dumps(ORACLE, separators=(",", ":"))
    assert_bad(args)


@pytest.mark.parametrize("index,value", [(i, value) for i in (1, 2) for value in
    [None, (), [], ListSubclass(ALLOWED), ALLOWED * 2, [7], [ALLOWED[0]] * 33,
     [StringSubclass(ALLOWED[0])]]])
def test_path_container_contract(index, value):
    args = good()
    args[index] = value
    assert_bad(args)


@pytest.mark.parametrize("path", ["/scripts/source.py", "scripts/../source.py", "scripts/./source.py",
    "scripts//source.py", "scripts\\source.py", "scripts/*.py", "scripts/a?.py", "scripts/[a].py",
    "scripts/{a}.py", ".git/x.py", "secrets/x.py", "scripts/x\x00.py", "scripts/x.bin",
    "scripts/" + "x" * 118 + ".py", "scripts/" + "é" * 59 + ".py", "scripts/\ud800.py"])
def test_invalid_literal_paths(path):
    args = good()
    args[1][0] = path
    args[2][0] = path
    profile = copy.deepcopy(ORACLE)
    profile["file_limits"] = {path: 12000, ALLOWED[1]: 24000}
    args[0][1] = declaration(profile)
    assert_bad(args)


@pytest.mark.parametrize("cap", [1, 160000])
def test_limit_boundaries_and_unicode_path_exact_128_bytes(cap):
    path = "scripts/" + "é" * 58 + "a.py"
    assert len(path.encode("utf-8")) == 128
    profile = {"version": "1.0", "file_limits": {path: cap}, "total_limit": cap}
    args = [[declaration(profile)], [path], [path]]
    frozen = copy.deepcopy(args)
    assert subject.parse_proposal_utf8_budget_profile(*args) == profile
    assert args == frozen
    bad_path = path.replace("a.py", "aa.py")
    assert len(bad_path.encode("utf-8")) == 129
    with pytest.raises(subject.ProposalByteProfileError):
        subject.parse_proposal_utf8_budget_profile(
            [declaration({**profile, "file_limits": {bad_path: cap}})], [bad_path], [bad_path])
    assert subject.parse_proposal_utf8_budget_profile(*args) == profile


def test_changed_subset_and_32_paths():
    paths = [f"scripts/x{i}.py" for i in range(32)]
    profile = {"version": "1.0", "file_limits": dict.fromkeys(paths, 1), "total_limit": 32}
    assert subject.parse_proposal_utf8_budget_profile([declaration(profile)], paths, paths[:1]) == profile
    args = good()
    args[2] = ["scripts/extra.py"]
    assert_bad(args)
    paths.append("scripts/x32.py")
    profile["file_limits"][paths[-1]] = 1
    with pytest.raises(subject.ProposalByteProfileError):
        subject.parse_proposal_utf8_budget_profile([declaration(profile)], paths, paths)


@pytest.mark.parametrize("helper", ["_unique_json", "_validate_overlay_path"])
def test_lower_exception_hidden_even_with_prior_caller_context(helper):
    args = good()
    assert_good(args)
    try:
        raise RuntimeError("prior caller marker")
    except RuntimeError:
        with patch.object(subject, helper, side_effect=RuntimeError("lower marker")):
            with pytest.raises(subject.ProposalByteProfileError) as caught:
                subject.parse_proposal_utf8_budget_profile(*args)
            assert str(caught.value) == ERROR and caught.value.__suppress_context__ is True
            assert caught.value.__cause__ is None
    assert_good(args)


@pytest.mark.parametrize("helper", ["_unique_json", "_validate_overlay_path"])
@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit, Interrupt])
def test_base_exception_identity_preserved(helper, kind):
    args = good()
    assert_good(args)
    marker = kind("original sentinel")
    with patch.object(subject, helper, side_effect=marker):
        with pytest.raises(kind) as caught:
            subject.parse_proposal_utf8_budget_profile(*args)
        assert caught.value is marker
    assert_good(args)
