"""Pure JSON/factory regression tests; no returned SDK function is executed."""
from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import math
from pathlib import Path
from types import FunctionType
import unicodedata

import pytest

from scripts import two_site_json_prompt_fidelity as fidelity


def test_json_without_format_characters_is_byte_identical():
    value = {"source": "quote\" slash\\ newline\n tab\t \u0641\u0627\u0631\u0633\u06cc \U0001f642",
             "numbers": [None, True, False, -0.0, 1, 1.25], "literal_escape": "\\u200c"}
    expected = json.dumps(value, ensure_ascii=False)
    assert fidelity.encode_context_json(value) == expected
    assert fidelity.encode_context_json(value).encode("utf-8") == expected.encode("utf-8")


def test_nested_source_diff_and_keys_round_trip_without_modification():
    source = "def f():\n    return '\u0645\u06cc\u200c\u0631\u0648\u062f'\n"
    diff = "@@ -1 +1 @@\n-before\u200cvalue\n+after\u200cvalue\n"
    value = {"sources": {"module.py": {"content": source}}, "proposal": {"unified_diff": diff},
             "nested": [{"key\u200cpart": ["\u200c", "\u2060"]}]}
    before = copy.deepcopy(value)
    result = fidelity.encode_context_json(value)
    assert json.loads(result) == value == before
    assert json.loads(result)["sources"]["module.py"]["content"].encode() == source.encode()
    assert json.loads(result)["proposal"]["unified_diff"].encode() == diff.encode()
    assert not any(unicodedata.category(character) == "Cf" for character in result)


@pytest.mark.parametrize("character", ["\u061c", "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff",
                                      "\U0001bca0", "\U000e0001"])
def test_only_format_characters_become_standard_json_escapes(character):
    assert unicodedata.category(character) == "Cf"
    value = {"before": "\u0641\u0627", "value": character, "after": "\U0001f642"}
    original = json.dumps(value, ensure_ascii=False)
    escape = json.dumps(character, ensure_ascii=True)[1:-1]
    result = fidelity.encode_context_json(value)
    assert result == original.replace(character, escape)
    assert json.loads(result) == value


def test_all_467_observed_joiners_survive_the_modeled_cli_filter():
    value = {"sources": {"body": "a\u200cb" * 424}, "proposal": {"unified_diff": "c\u200cd" * 43}}
    original = json.dumps(value, ensure_ascii=False)
    assert original.count("\u200c") == 467
    assert json.loads(original.replace("\u200c", "")) != value
    encoded = fidelity.encode_context_json(value)
    assert "\u200c" not in encoded and encoded.count("\\u200c") == 467
    assert json.loads(encoded.replace("\u200c", "")) == value
    assert len(encoded.encode()) - len(original.encode()) == 467 * 3


def test_supplementary_format_character_uses_surrogate_pair():
    result = fidelity.encode_context_json({"value": "\U000e0001"})
    assert "\\udb40\\udc01" in result and "\U000e0001" not in result
    assert json.loads(result) == {"value": "\U000e0001"}


def test_complete_unicode_format_category_round_trips():
    characters = "".join(chr(point) for point in range(0x110000)
                         if unicodedata.category(chr(point)) == "Cf")
    assert "\u200c" in characters and "\U000e0001" in characters
    expected = '{"all": "' + "".join(json.dumps(c, ensure_ascii=True)[1:-1] for c in characters) + '"}'
    result = fidelity.encode_context_json({"all": characters})
    assert result == expected and json.loads(result) == {"all": characters}
    assert not any(unicodedata.category(c) == "Cf" for c in result)


def test_existing_escape_text_is_data_and_not_double_escaped():
    value = {"literal": "\\u200c", "raw": "\u200c", "literal_pair": "\\udb40\\udc01"}
    result = fidelity.encode_context_json(value)
    assert json.loads(result) == value
    assert result == json.dumps(value, ensure_ascii=False).replace("\u200c", "\\u200c")


@pytest.mark.parametrize("field", ["source", "unified_diff"])
def test_roundtrip_gate_rejects_decoded_source_or_diff_loss(field):
    value = {"source": "before\u200cafter", "unified_diff": "-old\u200cvalue\n+new\u200cvalue\n"}
    original = json.dumps(value, ensure_ascii=False)
    lost = dict(value, **{field: value[field].replace("\u200c", "")})
    with pytest.raises(fidelity.PromptFidelityError, match="decoded JSON changed"):
        fidelity._require_json_equivalence(original, json.dumps(lost, ensure_ascii=False))


@pytest.mark.parametrize("value", ["\ud800", "\udfff", {"bad": "\ud800"}])
def test_lone_surrogates_fail_strict_utf8(value):
    with pytest.raises(UnicodeEncodeError):
        fidelity.encode_context_json(value)


def test_fixed_serialization_keyword_and_standard_nonfinite_behavior():
    with pytest.raises(fidelity.PromptFidelityError):
        fidelity.encode_context_json({"x": "\u200c"}, ensure_ascii=True)
    value = {"nan": float("nan"), "inf": float("inf"), "negative_inf": -float("inf"), "x": "\u200c"}
    result = json.loads(fidelity.encode_context_json(value, ensure_ascii=False))
    assert math.isnan(result["nan"]) and result["inf"] == float("inf")
    assert result["negative_inf"] == -float("inf") and result["x"] == "\u200c"


def test_encoded_187000_boundary_is_measured_without_truncation_or_waiver():
    overhead = len(fidelity.encode_context_json({"source": "\u200c"}).encode())
    exact_value = {"source": "x" * (187000 - overhead) + "\u200c"}
    exact = fidelity.encode_context_json(exact_value)
    assert len(exact.encode()) == 187000 and json.loads(exact) == exact_value
    plus_value = {"source": exact_value["source"] + "x"}
    plus = fidelity.encode_context_json(plus_value)
    assert len(plus.encode()) == 187001 and json.loads(plus) == plus_value
    # The encoder does not own or raise the unchanged external/SDK byte caps.


def _runner_and_source():
    from scripts import two_site_proposal_runner as runner
    source = fidelity._read_runner_source()
    assert hashlib.sha256(source).hexdigest() == fidelity.RUNNER_SOURCE_SHA256
    return runner, source


def _run_node(source):
    nodes = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "run"]
    assert len(nodes) == 1
    return nodes[0]


def test_factory_changes_only_fixed_call_and_two_private_namespace_bindings():
    runner, source = _runner_and_source()
    before = {name: id(value) for name, value in vars(runner).items()}
    original = runner.run

    def prior_loader(*args):
        raise AssertionError("factory must not execute prior_loader or SDK run")

    private = fidelity.make_root_review_run(prior_loader)
    assert type(private) is FunctionType and private is not original
    assert inspect.signature(private) == inspect.signature(original)
    assert private.__defaults__ is original.__defaults__
    assert private.__kwdefaults__ == original.__kwdefaults__ and private.__kwdefaults__ is not original.__kwdefaults__
    assert private.__annotations__ == original.__annotations__ and private.__closure__ is original.__closure__
    assert set(private.__globals__) - set(vars(runner)) == {fidelity._ENCODER_NAME}
    assert {name for name in vars(runner) if private.__globals__[name] is not vars(runner)[name]} == {"load_prior_worker"}
    assert private.__globals__["load_prior_worker"] is prior_loader
    assert private.__globals__[fidelity._ENCODER_NAME] is fidelity.encode_context_json
    assert before == {name: id(value) for name, value in vars(runner).items()} and runner.run is original
    original_node = _run_node(source)
    changed = fidelity._replace_context_call(original_node)
    names = [node for node in ast.walk(changed) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == fidelity._ENCODER_NAME]
    assert len(names) == 1 and names[0].lineno == 3190
    old_call = next(node for node in ast.walk(original_node) if isinstance(node, ast.Call)
                    and node.lineno == 3190 and isinstance(node.func, ast.Attribute) and node.func.attr == "dumps")
    names[0].func = copy.deepcopy(old_call.func)
    assert ast.dump(changed, include_attributes=True) == ast.dump(original_node, include_attributes=True)
    assert "2 * MAX_CONTEXT" in ast.get_source_segment(source.decode(), original_node)
    assert "capture_bounded_process" in private.__code__.co_names


@pytest.mark.parametrize("tamper", ["missing", "duplicate", "keyword", "location", "unrelated"])
def test_complete_ast_and_closed_call_tampering_fail(tamper):
    _, source = _runner_and_source()
    node = _run_node(source)
    target = next(part for part in ast.walk(node) if isinstance(part, ast.Call) and part.lineno == 3190
                  and isinstance(part.func, ast.Attribute) and part.func.attr == "dumps")
    if tamper == "missing":
        target.func.attr = "loads"
    elif tamper == "duplicate":
        node.body.append(ast.copy_location(ast.Expr(value=copy.deepcopy(target)), node.body[-1]))
    elif tamper == "keyword":
        target.keywords[0].value.value = True
    elif tamper == "location":
        target.lineno += 1
    else:
        node.body[0] = ast.copy_location(ast.Pass(), node.body[0])
    with pytest.raises(fidelity.PromptFidelityError):
        fidelity._replace_context_call(node)


def test_code_identity_is_independent_of_reference_sharing():
    runner, source = _runner_and_source()
    actual = runner.run.__code__
    expected = fidelity._compile_run(ast.parse(source))
    assert actual == expected and fidelity._same_code_identity(actual, expected)
    retained = (actual.co_code, actual.co_consts, actual.co_names, actual.co_varnames,
                actual.co_linetable, actual.co_exceptiontable)
    assert retained and fidelity._same_code_identity(actual, expected)
    assert fidelity._validated_run_code(source, runner.run).co_name == "run"


@pytest.mark.parametrize("attribute,value", [
    ("co_filename", "<wrong-source>"), ("co_firstlineno", 1),
    ("co_linetable", b""), ("co_exceptiontable", b""), ("co_stacksize", 0),
])
def test_explicit_code_source_and_debug_metadata_tampering_fails(attribute, value):
    runner, source = _runner_and_source()
    actual = runner.run.__code__
    changed = actual.replace(**{attribute: value})
    assert not fidelity._same_code_identity(changed, actual)
    fake = FunctionType(changed, runner.run.__globals__, runner.run.__name__,
                        runner.run.__defaults__, runner.run.__closure__)
    with pytest.raises(fidelity.PromptFidelityError):
        fidelity._validated_run_code(source, fake)


@pytest.mark.parametrize("attribute", ["co_filename", "co_qualname", "co_stacksize"])
def test_nested_code_source_identity_tampering_fails(attribute):
    source = "def outer():\n    def inner():\n        return 7\n    return inner\n"
    compiled = compile(source, "<pure-nested-code-fixture>", "exec", dont_inherit=True, optimize=0)
    reference = compile(source, "<pure-nested-code-fixture>", "exec", dont_inherit=True, optimize=0)
    actual = next(c for c in compiled.co_consts if isinstance(c, type(compiled)) and c.co_name == "outer")
    expected = next(c for c in reference.co_consts if isinstance(c, type(reference)) and c.co_name == "outer")
    assert actual == expected and fidelity._same_code_identity(actual, expected)
    index, child = next((i, c) for i, c in enumerate(actual.co_consts) if isinstance(c, type(actual)))
    value = child.co_stacksize + 1 if attribute == "co_stacksize" else "<wrong-nested-identity>"
    changed_child = child.replace(**{attribute: value})
    assert changed_child == child
    constants = list(actual.co_consts)
    constants[index] = changed_child
    changed = actual.replace(co_consts=tuple(constants))
    assert changed == actual
    assert not fidelity._same_code_identity(changed, expected)


def test_source_code_and_original_function_identity_tampering_fail():
    runner, source = _runner_and_source()
    with pytest.raises(fidelity.PromptFidelityError):
        fidelity._validated_run_code(source + b"\n", runner.run)

    def unrelated():
        raise AssertionError("unrelated function must not execute")

    with pytest.raises(fidelity.PromptFidelityError):
        fidelity._validated_run_code(source, unrelated)
    with pytest.raises(fidelity.PromptFidelityError):
        fidelity.make_root_review_run(lambda: None)


def test_drafts_and_active_helper_test_sources_contain_no_raw_format_characters():
    for path in (Path(fidelity.__file__), Path(__file__)):
        body = path.read_bytes()
        assert body.isascii()
        assert not any(unicodedata.category(character) == "Cf" for character in body.decode("utf-8"))
