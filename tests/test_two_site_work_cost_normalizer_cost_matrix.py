"""Original C176 cost/state catalogue."""
from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from pathlib import Path
from tests import test_two_site_work_cost_normalizer_case_data as data
from tests import test_two_site_work_cost_rows as core

literal = data.literal
token = data.token
PM, PU = data.PM, data.PU
FAMILIES = ("codex", "provider_token", "provider_shape", "count", "state", "cross")
COUNTS = (35, 48, 16, 21, 30, 26)
FAULTS = ("missing", "bool", "negative", "null", "string", "int_subclass", "overflow")
STATES = ("OBSERVED_CONTROLLER_ATTEMPTS", "OBSERVED_MATCHING_ATTEMPTS", "OK")
PROVIDER_STATES = STATES + ("PARTIAL", "unknown", "missing", "null", "list", "dict", "foreign_str", "foreign_class")
USAGE_STATES = ("valid", "unknown", "missing", "null", "list", "dict", "foreign_str", "foreign_class")


def faulty(kind, value=1):
    for name, tag in (("missing", ("remove",)), ("bool", literal(True)),
        ("negative", literal(-1)), ("null", literal(None)), ("string", literal("1")),
        ("int_subclass", ("foreign_int", value)), ("overflow", literal(1000000000001)),
        ("list", ("list", ())), ("dict", ("dict", ())),
        ("foreign_str", ("foreign_str", value)), ("foreign_class", ("foreign_class",))):
        if kind == name:
            return tag
    return literal(value if kind == "valid" else "UNKNOWN" if kind == "unknown" else kind)


def provider_expected(sub, full, pair, state="OBSERVED_CONTROLLER_ATTEMPTS"):
    flags = state in STATES[:2]
    return ((("provider_state",), literal(state)),
            (("group_eligible",), literal(flags)), (("observed_candidate",), literal(flags)),
            (PU + ("observed_subtotal",), literal(None) if sub is None else token(sub)),
            (PU + ("full_usage",), literal(None) if full is None else token(full)),
            (PU + ("attempt_pair",), literal(None) if pair is None else ("pair", pair)))


def raw_provider(full, sub, pair, state="OBSERVED_CONTROLLER_ATTEMPTS"):
    return ((PM + ("state",), literal(state)),
            (PM + ("usage",), ("remove",) if full is None else token(full)),
            (PM + ("observed_usage_subtotal",), token(sub)),
            (PM + ("attempts",), literal(pair[0])),
            (PM + ("usage_observed_attempts",), literal(pair[1])))


def catalogue():
    cases = []

    def add(family, layer, field, fault, context, raw, expected):
        cases.append(data.descriptor("C", family, layer, field, fault, context, tuple(path for path, tag in raw), raw, expected))

    for field, native in zip(data.C_KEYS, (100, 80, 20, 10)):
        for fault in FAULTS:
            add("codex", "root", field, fault, "flat", (((field,), faulty(fault, native)),),
                ((("codex_usage",), literal(None)),))
    for field, value in zip(data.C_KEYS[:3], (101, 81, 21)):
        add("codex", "root", field, "conservation", "flat", (((field,), literal(value)),), ((("codex_usage",), literal(None)),))
    add("codex", "root", "output_tokens", "500_valid", "flat", ((("output_tokens",), literal(500)),),
        ((("codex_usage",), ("codex", (100, 80, 20, 500))),))
    for name, vector in (("zero", (0, 0, 0, 0)),
                         ("maxcached", (1000000000000, 1000000000000, 0, 1000000000000)),
                         ("maxuncached", (1000000000000, 0, 1000000000000, 0))):
        add("codex", "root", "vector", name, "coherent", tuple(((k,), literal(v)) for k, v in zip(data.C_KEYS, vector)),
            ((("codex_usage",), ("codex", vector)),))
    for layer, key in (("full", "usage"), ("subtotal", "observed_usage_subtotal")):
        prefix = PM + (key,)
        context = "full_fault_sub14" if layer == "full" else "subtotal_fault_full_absent"
        base = () if layer == "full" else ((PM + ("usage",), ("remove",)),)
        negative = provider_expected("sub14" if layer == "full" else None, None, (1, 1))
        for field, native in zip(data.P_KEYS, (10, 4, 14)):
            for fault in FAULTS[1:] + ("arithmetic",):
                number = 11 if field == "input_tokens" else 5 if field == "output_tokens" else 999
                tag = literal(number) if fault == "arithmetic" else faulty(fault, native)
                add("provider_token", layer, field, fault, context, base + ((prefix + (field,), tag),), negative)
        for name in ("zero", "maxinput", "maxoutput"):
            add("provider_token", layer, "vector", name, context, base + ((prefix, token(name)),),
                provider_expected(name, name if layer == "full" else None, (1, 1)))
        for fault in data.MAP_FAULTS:
            add("provider_shape", layer, "map", fault, context, base + ((prefix, ("remove",) if fault == "absent" else ("shape", fault, "input_tokens")),), negative)
    for field in ("attempts", "usage_observed_attempts"):
        for fault in FAULTS[:-1] + ("33",):
            tag = literal(33) if fault == "33" else faulty(fault)
            add("count", "provider", field, fault, "pair", ((PM + (field,), tag),),
                provider_expected("sub14", None, None))
    for layer, field, context, path, states, native in (
        ("provider", "state", "provider_state", PM + ("state",), PROVIDER_STATES, STATES[0]),
        ("root", "usage_state", "codex_state", ("usage_state",), USAGE_STATES, "OBSERVED_COMPLETED_TURNS")):
        for state in states:
            if layer == "provider":
                valid = state in STATES or state == "PARTIAL"
                expected = provider_expected("sub14" if valid else None, "sub14" if state in STATES else None,
                    (1, 1) if valid else None, state if valid else None)
            else:
                expected = () if state == "valid" else ((("codex_usage",), literal(None)),)
            add("state", layer, field, state, context, ((path, faulty(state, native)),), expected)
    for state in ("CONTINUE", "COMPLETE"):
        for provider in STATES[:2]:
            raw = ((("state",), literal(state)), (PM + ("state",), literal(provider)))
            expected = ((("dimensions", "state"), literal(state)),) + provider_expected("sub14", "sub14", (1, 1), provider)
            add("state", "candidate", "state", state, provider, raw, expected)
    for state in ("PASS", "PASSED"):
        add("state", "candidate", "state", state, "gate", ((("state",), literal(state)),), ((("dimensions", "state"), literal(state)),
                (("observed_candidate",), literal(False))))
    role_cases = (("wrong_worker", "gemini_worker_run_id", "worker_run_id", "fixture-neutral-0"),
                  ("wrong_reviewer", "gemini_reviewer_run_id", "reviewer_run_id", "fixture-neutral-1"),
                  ("worker_both_words", "gemini_worker_run_id", "worker_run_id", "fixture-worker-reviewer-0"),
                  ("reviewer_both_words", "gemini_reviewer_run_id", "reviewer_run_id", "fixture-reviewer-worker-0"),
                  ("same_actor", "gemini_reviewer_run_id", "reviewer_run_id", "fixture-worker-0"))
    for name, field, dim, value in role_cases:
        add("state", "candidate", field, name, "role", (((field,), literal(value)),), ((("dimensions", dim), literal(value)),
                (("observed_candidate",), literal(False))))
    branches = (
        ("full120_sub14", "full120", "sub14", (1, 1), "full120", "full120", (1, 1)),
        ("bad_full_fallback", "bad", "sub14", (1, 1), "sub14", None, (1, 1)),
        ("missing_full_fallback", None, "sub14", (1, 1), "sub14", None, (1, 1)),
        ("both_malformed", "bad", "bad", (1, 1), None, None, (1, 1)),
        ("invalid_counts_known_full", "full120", "sub14", (33, 1), "full120", None, None),
        ("positive_full_0_0", "full120", "sub14", (0, 0), "full120", None, None),
        ("positive_full_1_0", "full120", "sub14", (1, 0), "full120", None, None),
        ("fullzero_1_0", "zero", "zero", (1, 0), "zero", None, (1, 0)),
    )
    count_cases = (
        ("observed_exceeds", "sub14", "sub14", (1, 2), "sub14", None, None),
        ("positive_full_1_0", "sub14", "sub14", (1, 0), "sub14", None, None),
        ("positive_full_0_0", "sub14", "sub14", (0, 0), "sub14", None, None),
        ("legal32_equal", "sub14", "sub14", (32, 32), "sub14", "sub14", (32, 32)),
        ("fullzero_0_0", "zero", "zero", (0, 0), "zero", "zero", (0, 0)),
        ("absent_full_zero_1_0", None, "zero", (1, 0), "zero", None, (1, 0)),
        ("legal32_1", "sub14", "sub14", (32, 1), "sub14", None, (32, 1)),
    )
    groups = (("count", "pair", "crossbinding", STATES[0], count_cases),)
    groups += tuple(("cross", "selection", state, state, branches) for state in STATES)
    groups += tuple(("cross", "selection", "additional", state,
        ((state, "full120", "sub14", (1, 1), selected, None,
          (1, 1) if selected else None),)) for state, selected in
        (("PARTIAL", "sub14"), ("UNKNOWN_STATE", None)))
    for family, field, context, state, rows in groups:
        for name, raw_full, raw_sub, pair, selected, full, expected_pair in rows:
            add(family, "provider", field, name, context, raw_provider(raw_full, raw_sub, pair, state),
                provider_expected(selected, full, expected_pair, None if state == "UNKNOWN_STATE" else state))
    return tuple(cases)


REFERENCE = catalogue()
DIGEST = "9e58cf4374d04c2df4534b61970dce37c7ff811f1e1c169b11470ad8275bff97"


def _setup():
    assert core.rows.MAX_NUMERIC == 1000000000000
    assert tuple(sum(spec[1] == family for spec in REFERENCE) for family in FAMILIES) == COUNTS
    assert core.rows.GROUPING_PROVIDER_STATES == frozenset(STATES[:2])
    for field, value in (("worker", "fixture-neutral-0"), ("reviewer", "fixture-neutral-1"),
                         ("worker", "fixture-worker-reviewer-0"), ("reviewer", "fixture-reviewer-worker-0")):
        assert core.rows._SAFE_ID_RE.fullmatch(value) is not None
        fn = core.cycle_metrics._is_gemini_worker_run_id if field == "worker" else core.cycle_metrics._is_gemini_reviewer_run_id
        assert fn(value) is False


def joint(plan, second):
    if second:
        case = next(case for case in plan if case[0][1] == "cross" and case[0][4] == "full120_sub14")
        return tuple((parent, key, dict.fromkeys(data.P_KEYS, 0)) for parent, key in
            ((case[1]["provider_metrics"], "usage"), (case[2]["provider_usage"], "observed_subtotal"),
             (case[2]["provider_usage"], "full_usage")))
    case = next(case for case in plan if case[0][1] == "codex" and case[0][4] == "maxcached")
    return tuple((parent, field, 500) for parent in (case[1], case[2]["codex_usage"])
                 for field in ("input_tokens", "cached_input_tokens"))


def test_C176_matrix_and_guards():
    data.seal(REFERENCE, 176, DIGEST)
    _setup()
    plan = [data.materialize(spec) for spec in REFERENCE]
    completed = data.execute(plan, REFERENCE, "C")
    data.countercheck(plan, completed, REFERENCE, "C", joint)


def test_used_arithmetic_omission():
    fault, oracle = core.fixtures.make_valid_raw("A"), core.fixtures.make_expected_row("A")
    fault["provider_metrics"]["usage"] = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 999}
    oracle["provider_usage"]["full_usage"] = None
    def passthrough(value, original):
        valid = original(value)
        if valid is not None:
            return valid
        if type(value) is dict and len(value) == 3 and all(type(key) is str for key in value):
            if set(value) == set(data.P_KEYS) and all(type(value[k]) is int and 0 <= value[k] <= 1000000000000 for k in data.P_KEYS):
                return {key: value[key] for key in data.P_KEYS}
        return None

    data.omission_witness(core.rows, "_validate_token_map", fault, oracle,
                          passthrough, fault["provider_metrics"]["usage"])


def test_cost_byte_budgets():
    root = Path(__file__).parent
    sizes = tuple(len((root / name).read_bytes()) for name in (
        "test_two_site_work_cost_normalizer_case_data.py", "test_two_site_work_cost_normalizer_identity_matrix.py",
        "test_two_site_work_cost_normalizer_cost_matrix.py"))
    assert all(size > 0 for size in sizes) and sum(sizes) <= 48000
    pair = sizes[0] + sizes[2]
    assert pair <= 24000
    for base, cap in ((21755, 48000), (15419, 41000), (26639, 53000)):
        assert base + pair <= cap
    raw = Path(__file__).read_bytes()
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert all(line == line.rstrip(b" \t\r") for line in raw.splitlines())
