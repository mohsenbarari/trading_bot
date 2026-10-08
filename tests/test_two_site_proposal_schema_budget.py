"""Codex-authored projection proofs under the owner's test exception."""
import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import copy
import json
import jsonschema
import pytest
from scripts import two_site_proposal_schema_budget as subject
from scripts.two_site_proposal_byte_profile import PREFIX, ERROR, ProposalByteProfileError
from scripts.two_site_proposal_transport import build_changes_schema

FILES = ["scripts/helper.py", "tests/test_helper.py"]
SHA = ["a" * 64, "b" * 64]
CAPS = (6000, 10000)
PROFILE = {"version": "1.0", "file_limits": dict(zip(FILES, CAPS)), "total_limit": 16000}
NEW, FULL, FORCED, REPLACE, FORCED_NEW = range(5)
REPLACEMENTS = {
    "type": "array", "minItems": 1, "maxItems": 32,
    "items": {"type": "object", "additionalProperties": False,
              "required": ["old", "new"],
              "properties": {"old": {"type": "string"}, "new": {"type": "string"}}}}


def declaration(profile=PROFILE):
    return PREFIX + json.dumps(profile, sort_keys=True, separators=(",", ":"))


REQ = ["ordinary requirement", declaration()]


def pin(*values):
    refs, pending = [], list(values)
    while pending:
        value = pending.pop()
        if type(value) in (dict, list):
            refs.append(value)
        if type(value) is dict:
            pending.extend(value.values())
        elif type(value) in (list, tuple):
            pending.extend(value)
    return json.dumps(values, allow_nan=False), refs


def unchanged(saved, *values):
    current = pin(*values)
    assert current[0] == saved[0]
    assert [id(n) for n in current[1]] == [id(n) for n in saved[1]]


def whole_oracle(result, expected):
    assert result == expected
    assert json.dumps(result, sort_keys=True) == json.dumps(expected, sort_keys=True)


def checked(schema, requirements=REQ, expected=None):
    saved = pin(schema, requirements, FILES, expected)
    try:
        result = subject.project_worker_schema_budget(schema, requirements, FILES)
        if expected is not None:
            whole_oracle(result, expected)
        second = subject.project_worker_schema_budget(schema, requirements, FILES)
        for other in (schema, requirements, FILES, expected, second):
            assert not {id(n) for n in pin(result)[1]} & {id(n) for n in pin(other)[1]}
        for node in pin(result)[1]:
            if type(node) is dict:
                node["poison"] = True
            else:
                node.clear()
        if expected is not None:
            whole_oracle(second, expected)
        return second
    finally:
        unchanged(saved, schema, requirements, FILES, expected)


def expected(*rows, capped=True):
    """Independent complete branch trees: index, transport mode, optional cap."""
    branches = []
    for index, kind, *limit in rows:
        full = kind != REPLACE
        content = {"type": "string" if full else "null"}
        if kind in (FORCED, FORCED_NEW):
            content["minLength"] = 1
        if limit or (full and capped):
            content["maxLength"] = limit[0] if limit else CAPS[index]
        branches.append(dict(type="object", additionalProperties=False,
            required=["path", "original_sha256", "content", "replacements"],
            properties=dict(path={"enum": [FILES[index]]}, content=content,
                original_sha256={"type": "null"} if kind in (NEW, FORCED_NEW) else {"enum": [SHA[index]]},
                replacements={"type": "array", "maxItems": 0} if full else copy.deepcopy(REPLACEMENTS))))
    return {"type": "array", "items": {"anyOf": branches}}


def real(contents=(None, None), forced=(), role="worker"):
    sources = {path: {"sha256": None if text is None else SHA[i], "content": text}
               for i, (path, text) in enumerate(zip(FILES, contents))}
    saved = pin(sources, FILES)
    try:
        return build_changes_schema(role, FILES, sources, force_full_paths=forced)
    finally:
        unchanged(saved, sources, FILES)


def props(schema, index=0):
    return schema["items"]["anyOf"][index]["properties"]


MIXED = expected((0, NEW), (1, NEW))


def reject(bad, requirements=REQ, error_type=subject.BudgetSchemaError):
    good = real()
    checked(good, expected=MIXED)
    with pytest.raises(error_type) as error:
        checked(bad, requirements)
    assert type(error.value) is error_type
    assert str(error.value) == (ERROR if error_type is ProposalByteProfileError else
                                "invalid proposal budget schema")
    checked(good, expected=MIXED)


@pytest.mark.parametrize("contents,forced,modes", [
    ((None, None), (), [(0, NEW), (1, NEW)]),
    (("x", "y"), tuple(FILES), [(0, FORCED), (1, FORCED)]),
    (("x" * 8192, "y" * 9000), tuple(FILES), [(0, FORCED), (1, FORCED)]),
    (("x", "y"), (FILES[0],), [(0, FORCED), (1, FULL), (1, REPLACE)]),
    (("x", "y" * 8192), (), [(0, FULL), (0, REPLACE), (1, REPLACE)]),
    (("x" * 8192, "y"), (), [(0, REPLACE), (1, FULL), (1, REPLACE)]),
])
def test_schema_matrix(contents, forced, modes):
    checked(real(contents, forced), expected=expected(*modes))


@pytest.mark.parametrize("index,old,wanted", [
    (0, 0, 0), (0, 17, 17), (0, 6000, 6000), (0, 7000, 6000),
    (1, 0, 0), (1, 7, 7), (1, 10000, 10000), (1, 10001, 10000)])
def test_tighter_limits(index, old, wanted):
    schema = real(forced=tuple(FILES))
    props(schema, index)["content"]["maxLength"] = old
    oracle = expected(*((i, FORCED_NEW, wanted if i == index else cap)
                        for i, cap in enumerate(CAPS)))
    checked(schema, expected=oracle)


def test_null_limit_unchanged():
    schema = real(("x", "y" * 8192))
    props(schema, 1)["content"]["maxLength"] = 23
    checked(schema, expected=expected((0, FULL), (0, REPLACE, 23), (1, REPLACE)))


@pytest.mark.parametrize("schema,requirements,oracle", [
    (real(), REQ, MIXED),
    (real(("x", "y")), [], expected((0, FULL), (0, REPLACE), (1, FULL), (1, REPLACE), capped=False)),
    (real(role="verifier"), [], {
        "type": "array", "maxItems": 0, "items": {"type": "object", "additionalProperties": False}}),
])
def test_nested_detachment(schema, requirements, oracle):
    result = checked(schema, requirements, oracle)
    checked(schema, requirements, oracle)
    whole_oracle(result, oracle)


def payload(index, text, sha=None, replacements=()):
    return {"path": FILES[index], "original_sha256": sha,
            "content": text, "replacements": list(replacements)}


def test_character_boundaries():
    schema = checked(real(), expected=MIXED)
    for index, size in enumerate(CAPS):
        jsonschema.validate([payload(index, "x" * size)], schema)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate([payload(index, "x" * (size + 1))], schema)
    euro = "€" * 10000
    assert len(euro) == 10000 and len(euro.encode("utf-8")) == 30000
    jsonschema.validate([payload(1, euro)], schema)
    # Character validation does not grant UTF-8/aggregate/acceptance permission.
    jsonschema.validate([payload(0, None, SHA[0], [{"old": "old", "new": "new"}])],
                        checked(real(("x", "y"))))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate([payload(0, "")], checked(real(forced=tuple(FILES))))


REMOVE = object()
ANYOF = ("items", "anyOf")
LATER = ANYOF + (1, "properties")
SCHEMA_FAULTS = [(LATER + tail, value) for tail, values in [
    ((), (REMOVE, None)),
    (("path",), ({"type": "string"}, REMOVE, None)),
    (("content",), (REMOVE, None)),
    (("content", "type"), (REMOVE, ["string", "null"])),
    (("path", "enum"), (REMOVE, [], FILES, ["scripts/unknown.py"])),
    (("content", "maxLength"), (True, False, -1, 1.5, None, "7")),
] for value in values]
SCHEMA_FAULTS += [
    (("type",), "object"), (("items",), None), (ANYOF, REMOVE),
    (ANYOF, []), (ANYOF, tuple(real()["items"]["anyOf"])),
    (ANYOF + (1,), None), ((), real(role="verifier")),
]
PROFILE_FAULTS = [
    [declaration(), declaration()], [declaration() + " "],
    [declaration({**PROFILE, "file_limits": {FILES[0]: 6000}})],
    [declaration().replace('"1.0"', '"1.0","version":"1.0"')],
]


@pytest.mark.parametrize("requirements", PROFILE_FAULTS)
def test_bad_profile(requirements):
    reject(real(), requirements, ProposalByteProfileError)


@pytest.mark.parametrize("path,value", SCHEMA_FAULTS)
def test_bad_schema(path, value):
    schema = real()
    if not path:
        schema = copy.deepcopy(value)
    else:
        target = schema
        for key in path[:-1]:
            target = target[key]
        if value is REMOVE:
            del target[path[-1]]
        else:
            target[path[-1]] = copy.deepcopy(value)
    reject(schema)


def test_parser_mutation_and_finally_restore():
    schema, expected = real(), copy.deepcopy(MIXED)
    saved = pin(schema, REQ, FILES, expected)
    checked(schema, expected=expected)
    original = subject.parse_proposal_utf8_budget_profile
    calls = []

    def wrong_cap(*args):
        result = original(*args)
        calls.append(True)
        result["file_limits"][FILES[1]] = 10001
        return result

    try:
        subject.parse_proposal_utf8_budget_profile = wrong_cap
        with pytest.raises(AssertionError):
            checked(schema, expected=expected)
        assert calls == [True]
    finally:
        subject.parse_proposal_utf8_budget_profile = original
        unchanged(saved, schema, REQ, FILES, expected)
    assert subject.parse_proposal_utf8_budget_profile is original
    checked(schema, expected=expected)
