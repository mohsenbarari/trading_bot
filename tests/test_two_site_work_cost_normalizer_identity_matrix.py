"""Complete original B194 identity catalogue at the unchanged normalizer."""
from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from pathlib import Path
from tests import test_two_site_work_cost_normalizer_case_data as data
from tests import test_two_site_work_cost_rows as core

h = core.h
literal = data.literal
SEMANTIC = ("stage_id", "scenario_key", "gap_reference", "source_sha256", "fingerprint")
WORK = ("schema_version", "fingerprint")
LAYERS = (
    ("top_semantic", ("semantic_identity",), ("semantic_identity",), SEMANTIC),
    ("top_work", ("work_lineage",), ("work_lineage",), WORK),
    ("nested_semantic", ("provider_metrics", "semantic_identity"),
     ("nested_identities", "semantic", "value"), SEMANTIC),
    ("nested_work", ("provider_metrics", "work_lineage"),
     ("nested_identities", "work", "value"), WORK),
)
FAMILIES = ("field", "shape", "length", "gap", "hash", "version", "presence")
COUNTS = (70, 32, 6, 28, 42, 6, 10)
IDENTITY_FAULTS = ("missing", "null", "wrong_native_type", "subclass", "invalid")
GAP_FAULTS = ("utf8_501", "traversal", "ascii_control", "c1_control", "backslash",
              "absolute", "drive", "empty_anchor", "multiple_anchor", "empty_path",
              "leading_space", "trailing_space", "surrogate")
HASH_FAULTS = ("empty", "short63", "long65", "uppercase64", "nonhex64", "newline64", "foreign_str64")


def invalid(layer, output):
    if layer.startswith("top"):
        identity = ((output, literal(None)),)
    else:
        identity = ((output[:-1] + ("state",), literal("INVALID")),
                    (output, literal(None)))
    return identity + ((("group_eligible",), literal(False)),
                       (("observed_candidate",), literal(False)))


def field_payload(field, fault):
    if fault == "missing":
        return ("remove",)
    if fault == "null":
        return literal(None)
    if fault == "wrong_native_type":
        return literal("1" if field == "schema_version" else 7)
    if fault == "subclass":
        if field == "schema_version":
            return ("foreign_int", 1)
        value = next(value for key, value in (
            ("stage_id", "P1-00b"), ("scenario_key", "ae00_provider_identity_tests"),
            ("gap_reference", "docs/ref#semantic"), ("source_sha256", "a" * 64),
            ("fingerprint", "b" * 64)) if key == field)
        return ("foreign_str", value)
    return literal(2 if field == "schema_version" else "-bad" if field in
        ("stage_id", "scenario_key") else "../ref#anchor" if field == "gap_reference" else "g" * 64)


def gap_value(fault):
    return next(value for key, value in (
        ("utf8_501", "docs/" + "é" * 247 + "#x"),
        ("traversal", "../ref#anchor"), ("ascii_control", "docs/r\x1f#x"),
        ("c1_control", "docs/r\x85#x"), ("backslash", "docs\\ref#x"),
        ("absolute", "/docs/ref#x"), ("drive", "C:/docs/ref#x"),
        ("empty_anchor", "docs/ref#"), ("multiple_anchor", "docs/ref#x#y"),
        ("empty_path", "#anchor"), ("leading_space", " docs/ref#x"),
        ("trailing_space", "docs/ref#x "), ("surrogate", "docs/\ud800#x")) if key == fault)


def catalogue():
    cases = []

    def add(family, layer, field, fault, paths, raw, expected, context="identity"):
        cases.append(data.descriptor("B", family, layer, field, fault, context, paths, raw, expected))

    for layer, rawpath, outpath, fields in LAYERS:
        for field in fields:
            for fault in IDENTITY_FAULTS:
                path = rawpath + (field,)
                add("field", layer, field, fault, (path,), ((path, field_payload(field, fault)),), invalid(layer, outpath))
    for layer, rawpath, outpath, fields in LAYERS:
        for fault in data.MAP_FAULTS:
            raw = ((rawpath, ("remove",) if fault == "absent" else ("shape", fault, fields[0])),)
            expected = invalid(layer, outpath)
            if fault == "absent" and layer.startswith("nested"):
                expected = ((outpath[:-1] + ("state",), literal("ABSENT")),
                            (outpath, literal(None)))
            add("shape", layer, "map", fault, (rawpath,), raw, expected)
    for layer, rawpath, outpath, fields in (LAYERS[0], LAYERS[2]):
        for field in SEMANTIC[:2]:
            path = rawpath + (field,)
            add("length", layer, field, "129", (path,), ((path, literal("a" * 129)),), invalid(layer, outpath))
    for field in SEMANTIC[:2]:
        paths = (("semantic_identity", field), ("provider_metrics", "semantic_identity", field))
        value = literal("a" * 128)
        expected = ((("semantic_identity", field), value),
                    (("nested_identities", "semantic", "value", field), value))
        add("length", "coherent", field, "128", paths, tuple((p, value) for p in paths), expected, "positive")
    for layer, rawpath, outpath, fields in (LAYERS[0], LAYERS[2]):
        for fault in GAP_FAULTS:
            path = rawpath + ("gap_reference",)
            add("gap", layer, "gap_reference", fault, (path,),
                ((path, literal(gap_value(fault))),), invalid(layer, outpath))
    for fault, value in (("ascii_500", "docs/" + "a" * 492 + "#xx"),
                         ("utf8_500", "docs/" + "é" * 246 + "#xx")):
        paths = (("semantic_identity", "gap_reference"), ("provider_metrics", "semantic_identity", "gap_reference"))
        expected = ((("semantic_identity", "gap_reference"), literal(value)),
                    (("nested_identities", "semantic", "value", "gap_reference"), literal(value)))
        add("gap", "coherent", "gap_reference", fault, paths,
            tuple((p, literal(value)) for p in paths), expected, "positive")
    for layer, rawpath, outpath, fields in LAYERS:
        for field in ("source_sha256", "fingerprint") if layer.endswith("semantic") else ("fingerprint",):
            for fault in HASH_FAULTS:
                value = next(value for key, value in (("empty", ""), ("short63", "a" * 63),
                    ("long65", "a" * 65), ("uppercase64", "A" * 64), ("nonhex64", "g" * 64),
                    ("newline64", "a" * 63 + "\n"), ("foreign_str64", "a" * 64)) if key == fault)
                tag = ("foreign_str", value) if fault == "foreign_str64" else literal(value)
                path = rawpath + (field,)
                add("hash", layer, field, fault, (path,), ((path, tag),), invalid(layer, outpath))
    for layer, rawpath, outpath, fields in (LAYERS[1], LAYERS[3]):
        for fault, value in (("bool_true", True), ("native_float1", 1.0), ("native_int2", 2)):
            path = rawpath + ("schema_version",)
            add("version", layer, "schema_version", fault, (path,), ((path, literal(value)),), invalid(layer, outpath))
    for layer, rawpath, outpath, fields in LAYERS[2:]:
        for fault in ("ABSENT", "present_null_INVALID", "present_badmap_INVALID", "VALID_mismatch", "VALID_bad_provider"):
            expected = invalid(layer, outpath)
            if fault == "ABSENT":
                raw = ((rawpath, ("remove",)),)
                expected = ((outpath[:-1] + ("state",), literal("ABSENT")), (outpath, literal(None)))
            elif fault == "present_null_INVALID":
                raw = ((rawpath, literal(None)),)
            elif fault == "present_badmap_INVALID":
                raw = ((rawpath, ("dict", ())),)
            elif fault == "VALID_mismatch":
                if layer == "nested_semantic":
                    values = (("stage_id", "P1-00b"), ("scenario_key", "ae00_provider_identity_tests"),
                        ("gap_reference", "docs/ref#semantic"),
                        ("source_sha256", "c3121fb027ccdf450c06ee58378b691d790e7210efb6d8b6c7cb26c3b887d22f"),
                        ("fingerprint", "438c0424c9f9604b269d6be92419ac6e572a0a1bd27088901780231e510c96cf"))
                else:
                    values = (("schema_version", 1), ("fingerprint", "f" * 64))
                raw = ((rawpath, ("dict", values)),)
                expected = ((outpath, ("dict", values)),
                    (("group_eligible",), literal(False)), (("observed_candidate",), literal(False)))
            else:
                raw = ((("provider_metrics", "state"), literal("UNKNOWN_STATE")),)
                expected = ((("provider_state",), literal(None)),
                    (("provider_usage", "observed_subtotal"), literal(None)),
                    (("provider_usage", "full_usage"), literal(None)),
                    (("provider_usage", "attempt_pair"), literal(None)),
                    (("group_eligible",), literal(False)), (("observed_candidate",), literal(False)))
            paths = (("provider_metrics", "state"),) if fault == "VALID_bad_provider" else (rawpath,)
            add("presence", layer, "map", fault, paths, raw, expected)
    return tuple(cases)


REFERENCE = catalogue()
DIGEST = "940511be624d601c50579c6473e0b8a841243edf79ace10f62ca3a0cc4257980"


def _setup(plan):
    assert core.rows.MAX_NUMERIC == 1000000000000
    assert core.rows.GROUPING_PROVIDER_STATES == frozenset(("OBSERVED_CONTROLLER_ATTEMPTS", "OBSERVED_MATCHING_ATTEMPTS"))
    assert tuple(sum(spec[1] == family for spec in REFERENCE) for family in FAMILIES) == COUNTS
    for spec, raw, expected, route, opaque in plan:
        for layer, rawpath, outpath, fields in LAYERS:
            oracle = data.at(expected, outpath)
            if oracle is not None:
                validator = core.cycle_metrics.semantic_identity if layer.endswith("semantic") else core.cycle_metrics.work_lineage
                h.assert_exact_typed(validator(data.at(raw, rawpath)), oracle)
    assert len(("docs/" + "a" * 492 + "#xx").encode()) == 500
    assert len(("docs/" + "é" * 246 + "#xx").encode()) == 500
    assert len(("docs/" + "é" * 247 + "#x").encode()) == 501


def joint(plan, second):
    kind = "utf8_500" if second else "ascii_500"
    case = next(case for case in plan if case[0][1] == "gap" and case[0][4] == kind)
    raw, expected = case[1:3]
    return tuple((parent, "gap_reference", "docs/changed#semantic") for parent in
        (raw["semantic_identity"], raw["provider_metrics"]["semantic_identity"],
         expected["semantic_identity"], expected["nested_identities"]["semantic"]["value"]))


def test_full_B194_and_used_metadata_guards():
    data.seal(REFERENCE, 194, DIGEST)
    plan = [data.materialize(spec) for spec in REFERENCE]
    _setup(plan)
    completed = data.execute(plan, REFERENCE, "B")
    data.countercheck(plan, completed, REFERENCE, "B", joint)


def test_reached_sdk_identity_omission_same_whole_oracle():
    fault, oracle = core.fixtures.make_valid_raw("A"), core.fixtures.make_expected_row("A")
    fault["semantic_identity"]["stage_id"] = "-bad"
    oracle["semantic_identity"] = None
    oracle["group_eligible"] = oracle["observed_candidate"] = False
    def omission(value, original):
        return dict(value)
    data.omission_witness(core.rows.cycle_metrics, "semantic_identity", fault, oracle,
                          omission, fault["semantic_identity"])


def test_identity_pair_physical_budget():
    shared = Path(data.__file__).read_bytes()
    family = Path(__file__).read_bytes()
    assert 0 < len(shared) <= 24000 and 0 < len(family) <= 24000
    assert len(shared) + len(family) <= 24000
    assert 11220 + 10535 + len(shared) + len(family) <= 48000
    assert 4884 + 10535 + len(shared) + len(family) <= 41000
    assert 11220 + 10535 + 4884 + len(shared) + len(family) <= 53000
    for raw in (shared, family):
        assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
        assert all(line == line.rstrip(b" \t\r") for line in raw.splitlines())
