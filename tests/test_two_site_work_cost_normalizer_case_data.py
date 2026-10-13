"""Immutable normalizer recipes and witnesses."""
from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from tests import test_two_site_work_cost_rows as core

h = core.h
C_KEYS = ("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens")
P_KEYS = ("input_tokens", "output_tokens", "total_tokens")
TOKENS = (("sub14", (10, 4, 14)), ("full120", (100, 20, 120)),
          ("bad", (10, 4, 999)), ("zero", (0, 0, 0)),
          ("maxinput", (1000000000000, 0, 1000000000000)),
          ("maxoutput", (0, 1000000000000, 1000000000000)))
MAP_FAULTS = ("absent", "null", "list", "extra", "missing", "integer_key", "foreign_key", "subclass")
PM = ("provider_metrics",)
PU = ("provider_usage",)


def literal(value):
    return ("literal", value)


def token(name):
    return ("tokens", next(vector for key, vector in TOKENS if key == name))


def immutable(value):
    if type(value) is tuple:
        return all(immutable(item) for item in value)
    return any(type(value) is typ for typ in (str, int, bool, float, type(None)))


def at(value, path):
    for key in path:
        value = value[key]
    return value


def payload(recipe, old=None):
    tag, *args = recipe
    if tag == "literal":
        return args[0]
    if tag == "tokens":
        return dict(zip(P_KEYS, args[0]))
    if tag == "codex":
        return dict(zip(C_KEYS, args[0]))
    if tag in ("pair", "list"):
        return list(args[0])
    if tag == "dict":
        return dict(args[0])
    if tag == "foreign_str":
        return h.ForeignStrKey(args[0])
    if tag == "foreign_int":
        return h.ForeignFinished(args[0])
    if tag == "foreign_class":
        return h.ForeignClassAllHooks
    if tag == "shape":
        kind, key = args
        if kind == "null":
            return None
        if kind == "list":
            return []
        if kind == "subclass":
            return h.ForeignDict(old)
        result = old.copy()
        if kind == "extra":
            result["extra"] = 1
        else:
            value = result.pop(key)
            if kind == "integer_key":
                result[7] = value
            elif kind == "foreign_key":
                result[h.ForeignStrKey(key)] = value
            else:
                assert kind == "missing"
        return result
    raise AssertionError("unknown recipe tag")


def apply_recipe(value, recipe):
    for path, tag in recipe:
        parent, key = at(value, path[:-1]), path[-1]
        if tag == ("remove",):
            parent.pop(key, None)
        else:
            parent[key] = payload(tag, parent.get(key))
    return value


def descriptor(route, family, layer, field, fault, context, paths, raw, expected):
    cid = "/".join((route, family, layer, field, fault, context))
    return (cid, family, layer, field, fault, context, paths, raw, expected, route)


def materialize(reference):
    raw = apply_recipe(core.fixtures.make_valid_raw("A"), reference[7])
    oracle = apply_recipe(core.fixtures.make_expected_row("A"), reference[8])
    opaque = []
    for path, tag in reference[7]:
        if tag[0].startswith("foreign") or tag[:2] == ("shape", "subclass"):
            opaque.append((path, at(raw, path)))
        if tag[:2] == ("shape", "foreign_key"):
            key = next(k for k in at(raw, path) if type(k) is h.ForeignStrKey)
            opaque.append((path + ("KEY",), key))
    return (reference, raw, oracle, reference[9], tuple(opaque))


def independent(value, recipe, opaque=()):
    for path, tag in recipe:
        parent, key = at(value, path[:-1]), path[-1]
        kind = tag[0]
        if kind == "remove":
            parent.pop(key, None)
            continue
        if kind == "literal":
            item = tag[1]
        elif kind in ("tokens", "codex"):
            keys = ("input_tokens", "output_tokens", "total_tokens") if kind == "tokens" else (
                "input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens")
            item = {key: tag[1][i] for i, key in enumerate(keys)}
        elif kind in ("dict", "list", "pair"):
            item = dict(tag[1]) if kind == "dict" else list(tag[1])
        elif kind.startswith("foreign") or tag[:2] == ("shape", "subclass"):
            item = next(obj for p, obj in opaque if p == path)
            expected_type = h.ForeignStrKey if kind == "foreign_str" else h.ForeignFinished if kind == "foreign_int" else h.ForeignDict if kind == "shape" else type(h.ForeignClassAllHooks)
            assert type(item) is expected_type
        else:
            shape, known = tag[1:]
            item = parent[key].copy()
            if shape == "null":
                item = None
            elif shape == "list":
                item = []
            elif shape == "extra":
                item["extra"] = 1
            else:
                old = item.pop(known)
                if shape != "missing":
                    replacement = 7 if shape == "integer_key" else next(obj for p, obj in opaque if p == path + ("KEY",))
                    item[replacement] = old
        parent[key] = item
    return value


def check_plan(plan, references, route):
    assert type(plan) is list and type(references) is tuple
    assert len(plan) == len(references)
    for case, expected in zip(plan, references):
        assert type(case) is tuple and len(case) == 5
        spec, raw, oracle, actual_route, opaque = case
        assert immutable(spec) and spec is expected and actual_route == route == expected[9]
        raw_reference = independent(core.fixtures.make_valid_raw("A"), expected[7], opaque)
        oracle_reference = independent(core.fixtures.make_expected_row("A"), expected[8])
        h.assert_custody_unchanged(raw, h.capture_custody(raw_reference))
        h.assert_exact_typed(oracle, oracle_reference)


def check_completed(completed, references):
    assert type(completed) is list and len(completed) == len(references)
    for actual, reference in zip(completed, references):
        assert type(actual) is tuple and actual == (reference[0], 3, True)
        assert type(actual[0]) is str and type(actual[1]) is int and type(actual[2]) is bool


def execute(plan, references, route):
    check_plan(plan, references, route)
    completed = []
    for spec, raw, expected, actual_route, opaque in plan:
        assert actual_route == route
        good, gold = core.fixtures.make_valid_raw("A"), core.fixtures.make_expected_row("A")
        # run_case returns after its three calls and custody finally.
        result = core.run_case(good, gold, raw, expected)
        assert type(result) is tuple and len(result) == 3 and h.get_total_hook_calls() == 0
        completed.append((spec[0], len(result), True))
    check_completed(completed, references)
    return completed


def countercheck(plan, completed, references, route, joint):
    original, done = tuple(plan), tuple(completed)
    custody = tuple((case[1], h.capture_custody(case[1]), case[2], h.capture_custody(case[2])) for case in original)

    def changed_id(item, cid):
        spec = (cid,) + item[0][1:]
        return (spec,) + item[1:]

    def swapped(i, j):
        spec = original[i][0][:7] + original[j][0][7:9] + original[i][0][9:]
        return (spec,) + original[j][1:3] + original[i][3:]

    attacks = (
        ((changed_id(original[0], "renamed"),) + original[1:], done, None),
        (original[1:], done, None),
        ((changed_id(original[1], "unique-replacement"),) + original[1:], done, None),
        ((original[0], original[0]) + original[2:], done, None),
        ((changed_id(original[0], original[1][0][0]), changed_id(original[1], original[0][0][0])) + original[2:], done, None),
        ((swapped(0, 1), swapped(1, 0)) + original[2:], done, None),
        ((original[0][:3] + (("C" if route == "B" else "B"),) + original[0][4:],) + original[1:], done, None),
        (original, (("wrong-completion", 3, True),) + done[1:], None),
        (original, done, False), (original, done, True),
    )
    for changed_plan, changed_done, second in attacks:
        undo = []
        try:
            plan[:] = changed_plan
            completed[:] = changed_done
            if second is not None:
                for parent, key, replacement in joint(plan, second):
                    undo.append((parent, key, parent[key]))
                    parent[key] = replacement
            with patch.object(core, "run_case", wraps=core.run_case) as calls, \
                 patch.object(core.rows, "normalize_work_cost_row", wraps=core.rows.normalize_work_cost_row) as source:
                try:
                    check_plan(plan, references, route)
                    check_completed(completed, references)
                except AssertionError:
                    pass
                else:
                    raise AssertionError("used checker accepted corruption")
                assert calls.call_count == source.call_count == 0
        finally:
            for parent, key, value in reversed(undo):
                parent[key] = value
                assert parent[key] is value
            plan[:] = original
            completed[:] = done
            assert all(a is b for a, b in zip(plan, original))
            assert all(a is b for a, b in zip(completed, done))
            for raw, rs, oracle, es in custody:
                h.assert_custody_unchanged(raw, rs)
                h.assert_custody_unchanged(oracle, es)
            check_plan(plan, references, route)
            check_completed(completed, references)
    execute(plan, references, route)


def seal(references, count, digest):
    assert type(references) is tuple and immutable(references) and len(references) == count
    assert len({item[0] for item in references}) == count
    raw = json.dumps(references, separators=(",", ":"), ensure_ascii=True).encode()
    assert hashlib.sha256(raw).hexdigest() == digest


def omission_witness(owner, method, fault, oracle, mutant, target):
    good, gold = core.fixtures.make_valid_raw("A"), core.fixtures.make_expected_row("A")
    frame = core.RetainedCallFrame([good, gold, fault, oracle], core._preflight)
    original = getattr(owner, method)
    reached = []

    def probe(value):
        reached.append(value)
        return mutant(value, original)

    try:
        core._call3(frame, good, gold, fault, oracle)
        with patch.object(owner, method, probe):
            try:
                core._call1(frame, fault, oracle)
            except AssertionError as exc:
                assert str(exc) == h._ERR_TYPED
            else:
                raise AssertionError("omission escaped whole oracle")
            assert any(value is target for value in reached) and h.get_total_hook_calls() == 0
        assert getattr(owner, method) is original
        frame.assert_all_custody()
        core._call3(frame, good, gold, fault, oracle)
    finally:
        assert getattr(owner, method) is original
        frame.assert_all_custody()


def test_immutable_recipes():
    assert immutable((C_KEYS, P_KEYS, TOKENS, MAP_FAULTS))
    assert 0 < len(Path(__file__).read_bytes()) <= 24000
