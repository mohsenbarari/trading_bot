"""Historical737K pairs plus exact explicit-print compatibility; no SDK run executes."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
from types import FunctionType

import pytest

from scripts import two_site_json_prompt_fidelity as fidelity
from tests import two_site_runner_752_fixture as seventh

FUTURE_RUNNER_SOURCE_SHA256 = "cb5f6fa91ce195e6dbd5dffa73183ddbe440e05829bbbdd100c9dfef015b7bf6"
FUTURE_RUN_AST_SHA256 = "9785eb03a32ac1f13cd6ea197e96b64101eb6c1bfa4d3a7859052a3508dfea94"
FUTURE_CONTEXT_LINE = 3237  # Fixed 737K context call line.
CAP_REPAIR_REPLACEMENTS_SHA256 = "d25b6835eb165347a8cfcaa9b8d475e103a0049afb628215f7ea836d3c216ecb"
CAP_REPAIR_REPLACEMENTS_JSON = "[\n  {\n    \"old\": \"try:\\n    from scripts.two_site_proposal_transport import (\\n        TransportPolicyError,\\n        build_changes_schema,\\n        validate_change_mode,\\n    )\\nexcept ImportError:\\n    from two_site_proposal_transport import (\\n        TransportPolicyError,\\n        build_changes_schema,\\n        validate_change_mode,\\n    )\\n\",\n    \"new\": \"try:\\n    from scripts.two_site_proposal_transport import (\\n        TransportPolicyError,\\n        build_changes_schema,\\n        classify_source,\\n        validate_change_mode,\\n    )\\nexcept ImportError:\\n    from two_site_proposal_transport import (\\n        TransportPolicyError,\\n        build_changes_schema,\\n        classify_source,\\n        validate_change_mode,\\n    )\\n\"\n  },\n  {\n    \"old\": \"def schema(\\n    role: str,\\n    task: dict | None = None,\\n    sources: dict | None = None,\\n    force_full_paths: tuple[str, ...] = (),\\n) -> dict:\\n    if (task is None) != (sources is None):\\n        raise ProposalError(\\\"task and sources must both be provided or both omitted\\\")\\n\",\n    \"new\": \"def _new_test_file_limits(task: dict, original_sources: dict) -> dict[str, int]:\\n    \\\"\\\"\\\"Classify canonical new tests from original bindings, never drafting hints.\\\"\\\"\\\"\\n    try:\\n        from scripts.two_site_proposal_byte_profile import (\\n            ProposalByteProfileError, parse_proposal_utf8_budget_profile,\\n        )\\n    except ImportError:\\n        from two_site_proposal_byte_profile import (\\n            ProposalByteProfileError, parse_proposal_utf8_budget_profile,\\n        )\\n    try:\\n        profile = parse_proposal_utf8_budget_profile(\\n            task.get(\\\"requirements\\\"), task[\\\"allowed_files\\\"], task[\\\"allowed_files\\\"]\\n        )\\n    except ProposalByteProfileError as exc:\\n        raise ProposalError(str(exc)) from None\\n    try:\\n        from scripts.two_site_task_context import _is_python_test_file\\n    except ImportError:\\n        from two_site_task_context import _is_python_test_file\\n    test_only = bool(task[\\\"allowed_files\\\"]) and all(\\n        _is_python_test_file(path) for path in task[\\\"allowed_files\\\"]\\n    )\\n    limits = {}\\n    if not isinstance(original_sources, dict):\\n        raise ProposalError(\\\"invalid original source bindings\\\")\\n    for path in task[\\\"allowed_files\\\"]:\\n        parts = PurePosixPath(path).parts\\n        if not (test_only or (len(parts) >= 2 and parts[0] == \\\"tests\\\"\\n                and parts[-1].startswith(\\\"test_\\\") and parts[-1].endswith(\\\".py\\\"))):\\n            continue\\n        try:\\n            is_new = classify_source(original_sources[path]) == \\\"new\\\"\\n        except (KeyError, TransportPolicyError):\\n            raise ProposalError(\\\"invalid original source bindings\\\") from None\\n        if is_new:\\n            cap = 24000\\n            if profile is not None:\\n                cap = min(cap, profile[\\\"file_limits\\\"][path], profile[\\\"total_limit\\\"])\\n            limits[path] = cap\\n    return limits\\n\\n\\ndef schema(\\n    role: str,\\n    task: dict | None = None,\\n    sources: dict | None = None,\\n    force_full_paths: tuple[str, ...] = (),\\n    *,\\n    original_sources: dict | None = None,\\n) -> dict:\\n    if (task is None) != (sources is None) or (task is None and original_sources is not None):\\n        raise ProposalError(\\\"task and sources must both be provided or both omitted\\\")\\n\"\n  },\n  {\n    \"old\": \"        if role == \\\"worker\\\":\\n            notes = task_preparation_notes(task, sources)\\n            out_contract = notes.get(\\\"output_contract\\\", {}) if isinstance(notes, dict) else {}\\n            if out_contract.get(\\\"test_only_task\\\"):\\n                new_tests = set(out_contract.get(\\\"new_test_files\\\") or [\\n                    p for p in task[\\\"allowed_files\\\"]\\n                    if sources.get(p, {}).get(\\\"content\\\") is None and sources.get(p, {}).get(\\\"sha256\\\") is None\\n                ])\\n                max_new_test = out_contract.get(\\\"max_new_test_file_utf8_bytes\\\", 24000)\\n                _apply_new_test_max_length(fields[\\\"changes\\\"], new_tests, max_new_test)\\n\",\n    \"new\": \"        if role == \\\"worker\\\":\\n            origin = sources if original_sources is None else original_sources\\n            for path, cap in _new_test_file_limits(task, origin).items():\\n                _apply_new_test_max_length(fields[\\\"changes\\\"], {path}, cap)\\n\"\n  },\n  {\n    \"old\": \"                props[\\\"content\\\"][\\\"maxLength\\\"] = max_len\\n\",\n    \"new\": \"                props[\\\"content\\\"][\\\"maxLength\\\"] = min(\\n                    props[\\\"content\\\"].get(\\\"maxLength\\\", max_len), max_len\\n                )\\n\"\n  },\n  {\n    \"old\": \"    enforce_raw_transport: bool = True,\\n) -> dict:\\n    if type(enforce_raw_transport) is not bool:\\n\",\n    \"new\": \"    enforce_raw_transport: bool = True,\\n    *,\\n    original_sources: dict | None = None,\\n) -> dict:\\n    if type(enforce_raw_transport) is not bool:\\n\"\n  },\n  {\n    \"old\": \"    notes = task_preparation_notes(task, sources)\\n    out_contract = notes.get(\\\"output_contract\\\", {}) if isinstance(notes, dict) else {}\\n    test_only = bool(out_contract.get(\\\"test_only_task\\\"))\\n    max_transport = out_contract.get(\\\"max_test_only_transport_bytes\\\", 64000)\\n    max_new_test = out_contract.get(\\\"max_new_test_file_utf8_bytes\\\", 24000)\\n    new_test_files = set(out_contract.get(\\\"new_test_files\\\", []))\\n\\n    if role == \\\"worker\\\" and test_only and enforce_raw_transport:\\n\",\n    \"new\": \"    origin = sources if original_sources is None else original_sources\\n    new_test_limits = _new_test_file_limits(task, origin) if role == \\\"worker\\\" else {}\\n    notes = task_preparation_notes(task, sources)\\n    out_contract = notes.get(\\\"output_contract\\\", {}) if isinstance(notes, dict) else {}\\n    test_only = bool(out_contract.get(\\\"test_only_task\\\"))\\n    max_transport = out_contract.get(\\\"max_test_only_transport_bytes\\\", 64000)\\n\\n    if role == \\\"worker\\\" and test_only and enforce_raw_transport:\\n\"\n  },\n  {\n    \"old\": \"        if role == \\\"worker\\\" and test_only:\\n            is_new = sources[name][\\\"sha256\\\"] is None and sources[name][\\\"content\\\"] is None\\n            if (is_new or name in new_test_files) and len(change[\\\"content\\\"].encode(\\\"utf-8\\\")) > max_new_test:\\n                raise ProposalError(f\\\"new test file content exceeds budget: {name}\\\")\\n\",\n    \"new\": \"        if name in new_test_limits:\\n            if len(change[\\\"content\\\"].encode(\\\"utf-8\\\")) > new_test_limits[name]:\\n                raise ProposalError(f\\\"new test file content exceeds budget: {name}\\\")\\n\"\n  },\n  {\n    \"old\": \"    expected_model: str = MODEL,\\n) -> tuple[dict, dict]:\\n    events = [unique_json(line) for line in raw.splitlines() if line.strip()]\\n\",\n    \"new\": \"    expected_model: str = MODEL,\\n    *,\\n    original_sources: dict | None = None,\\n) -> tuple[dict, dict]:\\n    events = [unique_json(line) for line in raw.splitlines() if line.strip()]\\n\"\n  },\n  {\n    \"old\": \"            result, task, sources, role, enforce_raw_transport=enforce_raw_transport\\n\",\n    \"new\": \"            result, task, sources, role, enforce_raw_transport=enforce_raw_transport,\\n            original_sources=original_sources,\\n\"\n  },\n  {\n    \"old\": \"    response_schema = schema(role, task, candidate_sources if candidate_sources is not None else sources, force_full_paths)\\n\",\n    \"new\": \"    response_schema = schema(\\n        role, task, candidate_sources if candidate_sources is not None else sources,\\n        force_full_paths, original_sources=sources,\\n    )\\n\"\n  },\n  {\n    \"old\": \"                raw, task, active_sources, role, force_full_paths, role_model\\n\",\n    \"new\": \"                raw, task, active_sources, role, force_full_paths, role_model,\\n                original_sources=sources,\\n\"\n  },\n  {\n    \"old\": \"                    rebased, task, sources, role, enforce_raw_transport=False\\n\",\n    \"new\": \"                    rebased, task, sources, role, enforce_raw_transport=False,\\n                    original_sources=sources,\\n\"\n  }\n]\n"

_OLD_SOURCE = "55cd2cc5f0dc8489514db8843d3e3114532d2beced0566b0456e2cf87d5c8af4"
_TEXT_SOURCE = "d786e58266893481c9bbd8447d0228170b2037f91073ac2b0ad447bc1f59367f"
_OLD_AST = "90afa2143a2172212027a01092aa80107cd1b5f19cc6bdf3349671c9add0232b"
_TEXT_AST = "8951f6c504fdfeeaaa047340f4b5557b581514e7d25da2bcafb938d42fa3197d"
_PRINT_SOURCE = "502b26fb4528349d8bf7d3c9c6f7b45eca59e681cb5d05cf71ed88847b7c1f52"
_PRINT_AST = "ccc7d3403a6f9ae8a1738a0ef558ff47af8358b0296a286124603d3dd98c06ad"
_EMPTY_PRINT_SOURCE = "f53373164b60f700452e1f2b9cbfb3b5e2670abf79b41a25d36d0ea131ef8ccb"
_EMPTY_PRINT_AST = "285043997d50c96e9dee72eb030ca82c44663f3ecfd6a5370318438ab6181551"
_GENERATION_COMMAND = b'command = [str(disposable_cli), "--mode", "plan", "--sandbox", "--model", role_model,'
_PRINT_COMMAND = b'command = [str(disposable_cli), "--print", "--mode", "plan", "--sandbox", "--model", role_model,'
_EMPTY_PRINT_COMMAND = b'command = [str(disposable_cli), "--print=", "--mode", "plan", "--sandbox", "--model", role_model,'

_NDJSON_SOURCE = "46544061459af3d0df38079abe21817bf59606341bdf6d9100fe9607e79e9cb4"
_NDJSON_AST = "78ff37fd7f19153e4671f2cb7a000718c4abb28a5c51da2f227df2f16ed733e7"
_NDJSON_CONTEXT_LINE = 3252
_NDJSON_REPLACEMENTS = [["def run(root: Path,","def _cli_stream_input(prompt: str, *, is_compact_review: bool = False) -> bytes:\n    \"\"\"Encode one unchanged logical prompt; wire expansion is a separate bound.\"\"\"\n    if type(prompt) is not str or not prompt or type(is_compact_review) is not bool:\n        raise ProposalError(\"invalid CLI input prompt\")\n    logical_limit = 160_000 if is_compact_review else 2 * MAX_CONTEXT\n    if len(prompt.encode(\"utf-8\")) > logical_limit:\n        raise ProposalError(\"CLI logical prompt exceeds budget\")\n    wire = (json.dumps({\"event\": \"user\", \"message\": {\"content\": prompt}}) + \"\\n\").encode()\n    # Default JSON string encoding expands each UTF8 input byte by at most six;\n    # the fixed envelope plus final LF is46 bytes. No logical cap is enlarged.\n    if len(wire) > 6 * logical_limit + 46:\n        raise ProposalError(\"CLI input wire exceeds derived budget\")\n    return wire\n\n\ndef run(root: Path,"],["    with proposal_cli_context(root, cli) as (local, disposable_cli, cli_source):","    stdin_bytes = _cli_stream_input(prompt, is_compact_review=is_compact_review)\n    with proposal_cli_context(root, cli) as (local, disposable_cli, cli_source):"],["command = [str(disposable_cli), \"--print=\", \"--mode\", \"plan\", \"--sandbox\", \"--model\", role_model,","command = [str(disposable_cli), \"--mode\", \"plan\", \"--sandbox\", \"--model\", role_model,"],["*effort_args, \"--input-format\", \"text\",","*effort_args, \"--input-format\", \"stream-json\","],["                    prompt.encode(\"utf-8\"),","                    stdin_bytes,"]]


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def _bound_sha(value):
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _require_bindings():
    assert _bound_sha(FUTURE_RUNNER_SOURCE_SHA256), "UNBOUND future Source pin"
    assert _bound_sha(FUTURE_RUN_AST_SHA256), "UNBOUND future run AST pin"
    assert _bound_sha(CAP_REPAIR_REPLACEMENTS_SHA256), "UNBOUND exact repair JSON pin"
    assert type(FUTURE_CONTEXT_LINE) is int and FUTURE_CONTEXT_LINE > 0, "UNBOUND context line"
    assert FUTURE_RUNNER_SOURCE_SHA256 not in {_OLD_SOURCE, _TEXT_SOURCE}
    assert FUTURE_RUN_AST_SHA256 not in {_OLD_AST, _TEXT_AST}


def _repair_replacements():
    _require_bindings()
    assert _digest(CAP_REPAIR_REPLACEMENTS_JSON.encode("utf-8")) == CAP_REPAIR_REPLACEMENTS_SHA256
    replacements = json.loads(CAP_REPAIR_REPLACEMENTS_JSON)
    assert isinstance(replacements, list) and len(replacements) >= 2
    for replacement in replacements:
        assert isinstance(replacement, dict) and set(replacement) == {"old", "new"}
        assert all(isinstance(replacement[key], str) and replacement[key]
                   for key in ("old", "new"))
        assert replacement["old"] != replacement["new"]
    return replacements


def _apply_repair(source, *, inverse=False):
    replacements = _repair_replacements()
    if inverse:
        replacements = list(reversed(replacements))
    result = source.decode("utf-8", errors="strict")
    for replacement in replacements:
        old, new = replacement["old"], replacement["new"]
        if inverse:
            old, new = new, old
        assert result.count(old) == 1, "fixed repair fragment is not unique"
        result = result.replace(old, new, 1)
    return result.encode("utf-8", errors="strict")


def _apply_ndjson_transport(source, *, inverse=False):
    replacements = _NDJSON_REPLACEMENTS
    result = source.decode("utf-8", errors="strict")
    for old, new in reversed(replacements) if inverse else replacements:
        if inverse:
            old, new = new, old
        assert result.count(old) == 1, "fixed NDJSON transport fragment is not unique"
        result = result.replace(old, new, 1)
    return result.encode("utf-8", errors="strict")


def text_baseline_if_needed(source):
    """Invert only exact known print/737K changes, never normalize unknown Source."""
    source_sha = _digest(source)
    if source_sha == seventh.SOURCE_752_SHA:
        source = seventh.apply_generation_completion_transport(source.decode(), inverse=True).encode()
        assert _digest(source) == _NDJSON_SOURCE
        assert _digest(seventh.apply_generation_completion_transport(source.decode()).encode()) == seventh.SOURCE_752_SHA
        source_sha = _digest(source)
    if source_sha == _NDJSON_SOURCE:
        source = _apply_ndjson_transport(source, inverse=True)
        assert _digest(source) == _EMPTY_PRINT_SOURCE
        assert _digest(_apply_ndjson_transport(source)) == _NDJSON_SOURCE
        source_sha = _digest(source)
    if source_sha == _EMPTY_PRINT_SOURCE:
        assert source.count(_EMPTY_PRINT_COMMAND) == 1
        source = source.replace(_EMPTY_PRINT_COMMAND, _PRINT_COMMAND, 1)
        source_sha = _digest(source)
        assert source_sha == _PRINT_SOURCE
    if source_sha == _PRINT_SOURCE:
        assert source.count(_PRINT_COMMAND) == 1
        source = source.replace(_PRINT_COMMAND, _GENERATION_COMMAND, 1)
        source_sha = _digest(source)
        assert source_sha == FUTURE_RUNNER_SOURCE_SHA256
    if source_sha in {_OLD_SOURCE, _TEXT_SOURCE}:
        return source
    _require_bindings()
    assert source_sha == FUTURE_RUNNER_SOURCE_SHA256, "unknown fixture Source"
    baseline = _apply_repair(source, inverse=True)
    assert _digest(baseline) == _TEXT_SOURCE
    assert _apply_repair(baseline) == source
    return baseline


def _run_node(source):
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name == "run"]
    assert len(nodes) == 1
    return nodes[0]


def _three_versions():
    from scripts import two_site_proposal_runner as runner

    _require_bindings()
    actual_source = fidelity._read_runner_source()
    source = text_baseline_if_needed(actual_source)
    command = b'*effort_args, "--input-format", "stream-json",'
    stdin = b'(json.dumps({"event": "user", "message": {"content": prompt}}) + "\\n").encode(),'
    text_command = b'*effort_args, "--input-format", "text",'
    text_stdin = b'prompt.encode("utf-8"),'
    if _digest(source) == _OLD_SOURCE:
        assert source.count(command) == source.count(stdin) == 1
        old = source
        text = old.replace(command, text_command).replace(stdin, text_stdin)
    else:
        assert _digest(source) == _TEXT_SOURCE
        assert source.count(text_command) == source.count(text_stdin) == 1
        text = source
        old = text.replace(text_command, command).replace(text_stdin, stdin)
    assert _digest(old) == _OLD_SOURCE and _digest(text) == _TEXT_SOURCE
    assert old.replace(command, text_command).replace(stdin, text_stdin) == text
    assert text.replace(text_command, command).replace(text_stdin, stdin) == old
    future = _apply_repair(text)
    assert _digest(future) == FUTURE_RUNNER_SOURCE_SHA256
    assert _apply_repair(future, inverse=True) == text
    versions = (old, text, future)
    expected = dict(zip((_OLD_SOURCE, _TEXT_SOURCE, FUTURE_RUNNER_SOURCE_SHA256),
                        (_OLD_AST, _TEXT_AST, FUTURE_RUN_AST_SHA256)))
    expected[_PRINT_SOURCE] = _PRINT_AST
    expected[_EMPTY_PRINT_SOURCE] = _EMPTY_PRINT_AST
    expected[_NDJSON_SOURCE] = _NDJSON_AST
    expected[seventh.SOURCE_752_SHA] = seventh.RUN_752_AST_SHA
    assert fidelity._RUNNER_IDENTITIES == expected
    assert fidelity._CONTEXT_CALL_LINES == {
        _OLD_AST: 3190, _TEXT_AST: 3190, FUTURE_RUN_AST_SHA256: FUTURE_CONTEXT_LINE,
        _PRINT_AST: 3237,
        _EMPTY_PRINT_AST: 3237,
        _NDJSON_AST: _NDJSON_CONTEXT_LINE,
        seventh.RUN_752_AST_SHA: seventh.CONTEXT_752_LINE,
    }
    for body in versions:
        run_ast = ast.dump(_run_node(body), include_attributes=True).encode()
        assert _digest(run_ast) == expected[_digest(body)]
    assert actual_source in versions or _digest(actual_source) in {
        _PRINT_SOURCE, _EMPTY_PRINT_SOURCE, _NDJSON_SOURCE, seventh.SOURCE_752_SHA,
    }
    if _digest(actual_source) in {_PRINT_SOURCE, _EMPTY_PRINT_SOURCE}:
        assert future.count(_GENERATION_COMMAND) == 1
        command = _EMPTY_PRINT_COMMAND if _digest(actual_source) == _EMPTY_PRINT_SOURCE else _PRINT_COMMAND
        assert future.replace(_GENERATION_COMMAND, command, 1) == actual_source
    if _digest(actual_source) == _NDJSON_SOURCE:
        empty = future.replace(_GENERATION_COMMAND, _EMPTY_PRINT_COMMAND, 1)
        assert _digest(empty) == _EMPTY_PRINT_SOURCE
        assert _apply_ndjson_transport(empty) == actual_source
    if _digest(actual_source) == seventh.SOURCE_752_SHA:
        empty = future.replace(_GENERATION_COMMAND, _EMPTY_PRINT_COMMAND, 1)
        ndjson = _apply_ndjson_transport(empty)
        assert _digest(ndjson) == _NDJSON_SOURCE
        assert seventh.apply_generation_completion_transport(ndjson.decode()).encode() == actual_source
    return runner, versions


def _unexecuted_original(runner, source):
    original = FunctionType(fidelity._compile_run(ast.parse(source)), runner.run.__globals__,
                            "run", runner.run.__defaults__, runner.run.__closure__)
    # FunctionType does not copy keyword-only defaults. Preserve the exact
    # shared signature without changing code identity or the actual SDK object.
    original.__kwdefaults__ = copy.deepcopy(runner.run.__kwdefaults__)
    return original


def test_existing_fixture_is_ascii_and_within_explicit_manual_limit():
    body = Path(__file__).read_bytes()
    # Source752's explicit existing-file manual slot, NOT public new-test caps.
    assert body.isascii() and len(body) <= 30000


@pytest.mark.parametrize("index", [0, 1, 2])
def test_all_three_exact_pairs_validate_and_preserve_complete_ast(index, tmp_path, monkeypatch):
    runner, versions = _three_versions()
    source = versions[index]
    original = _unexecuted_original(runner, source)
    before = {name: id(value) for name, value in vars(runner).items()}
    transformed_code = fidelity._validated_run_code(source, original)
    assert transformed_code.co_name == "run"
    node = _run_node(source)
    ast_sha = _digest(ast.dump(node, include_attributes=True).encode())
    line = fidelity._CONTEXT_CALL_LINES[ast_sha]
    transformed = fidelity._replace_context_call(node)
    targets = [part for part in ast.walk(transformed) if isinstance(part, ast.Call)
               and isinstance(part.func, ast.Name) and part.func.id == fidelity._ENCODER_NAME]
    assert len(targets) == 1 and targets[0].lineno == line
    old_call = next(part for part in ast.walk(node) if isinstance(part, ast.Call)
                    and part.lineno == line and isinstance(part.func, ast.Attribute)
                    and isinstance(part.func.value, ast.Name) and part.func.value.id == "json"
                    and part.func.attr == "dumps")
    targets[0].func = copy.deepcopy(old_call.func)
    assert ast.dump(transformed, include_attributes=True) == ast.dump(node, include_attributes=True)
    for attribute in ("co_linetable", "co_exceptiontable"):
        fake = FunctionType(original.__code__.replace(**{attribute: b""}), original.__globals__)
        with pytest.raises(fidelity.PromptFidelityError):
            fidelity._validated_run_code(source, fake)
    fixture = tmp_path / "known.py"
    fixture.write_bytes(source)
    with monkeypatch.context() as patcher:
        patcher.setattr(fidelity, "_RUNNER_PATH", fixture)
        assert fidelity._read_runner_source() == source
    assert before == {name: id(value) for name, value in vars(runner).items()}


@pytest.mark.parametrize("own,other", [(0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1)])
def test_all_crosspairs_reject_code_and_paired_ast_mismatch(own, other, monkeypatch):
    runner, versions = _three_versions()
    source = versions[own]
    wrong_function = _unexecuted_original(runner, versions[other])
    with pytest.raises(fidelity.PromptFidelityError, match="actual SDK run code mismatch"):
        fidelity._validated_run_code(source, wrong_function)
    wrong_ast = _digest(ast.dump(_run_node(versions[other]), include_attributes=True).encode())
    monkeypatch.setitem(fidelity._RUNNER_IDENTITIES, _digest(source), wrong_ast)
    with pytest.raises(fidelity.PromptFidelityError, match="paired SDK source/run AST pin mismatch"):
        fidelity._validated_run_code(source, _unexecuted_original(runner, source))


def test_future_newline_and_every_partial_repair_are_unknown(tmp_path, monkeypatch):
    runner, (_, text, future) = _three_versions()
    invalid = [future + b"\n"]
    for replacement in _repair_replacements():
        old, new = replacement["old"].encode(), replacement["new"].encode()
        assert text.count(old) == 1 and future.count(new) == 1
        invalid.append(text.replace(old, new, 1))
        invalid.append(future.replace(new, old, 1))
    for source in invalid:
        assert _digest(source) not in fidelity._RUNNER_IDENTITIES
        with pytest.raises(fidelity.PromptFidelityError, match="SDK source pin mismatch"):
            fidelity._validated_run_code(source, runner.run)
        fixture = tmp_path / "unknown.py"
        fixture.write_bytes(source)
        with monkeypatch.context() as patcher:
            patcher.setattr(fidelity, "_RUNNER_PATH", fixture)
            with pytest.raises(fidelity.PromptFidelityError, match="SDK source pin mismatch"):
                fidelity._read_runner_source()


def test_future_wrong_fixed_context_line_fails_closed(monkeypatch):
    _, versions = _three_versions()
    monkeypatch.setitem(fidelity._CONTEXT_CALL_LINES, FUTURE_RUN_AST_SHA256, FUTURE_CONTEXT_LINE + 1)
    with pytest.raises(fidelity.PromptFidelityError, match="CONTEXT JSON call shape drift"):
        fidelity._replace_context_call(_run_node(versions[2]))

def test_sixth_transport_exact_inverse_and_partial_changes_keep_exact_historical_identity():
    runner, _ = _three_versions()
    actual = fidelity._read_runner_source()
    if _digest(actual) == seventh.SOURCE_752_SHA:
        actual = seventh.apply_generation_completion_transport(actual.decode(), inverse=True).encode()
    assert _digest(actual) == _NDJSON_SOURCE
    empty = _apply_ndjson_transport(actual, inverse=True)
    assert _digest(empty) == _EMPTY_PRINT_SOURCE
    assert _apply_ndjson_transport(empty) == actual
    assert _digest(ast.dump(_run_node(actual), include_attributes=True).encode()) == _NDJSON_AST
    fidelity._validated_run_code(actual, _unexecuted_original(runner, actual))
    for old, new in _NDJSON_REPLACEMENTS:
        assert empty.count(old.encode()) == actual.count(new.encode()) == 1
        for partial in (empty.replace(old.encode(), new.encode(), 1),
                        actual.replace(new.encode(), old.encode(), 1)):
            # Removing only the print token from Source65 is the already
            # authenticated737K byte form, not an unknown partial migration.
            if _digest(partial) == FUTURE_RUNNER_SOURCE_SHA256:
                assert partial == _three_versions()[1][2]
                fidelity._validated_run_code(partial, _unexecuted_original(runner, partial))
                continue
            assert _digest(partial) not in fidelity._RUNNER_IDENTITIES
            with pytest.raises(fidelity.PromptFidelityError, match="SDK source pin mismatch"):
                fidelity._validated_run_code(partial, runner.run)


def test_seventh_exact_inverse_and_each_partial_hunk_remain_closed():
    runner, _ = _three_versions()
    current = fidelity._read_runner_source()
    assert _digest(current) == seventh.SOURCE_752_SHA
    old = seventh.apply_generation_completion_transport(current.decode(), inverse=True).encode()
    assert _digest(old) == _NDJSON_SOURCE
    assert seventh.apply_generation_completion_transport(old.decode()).encode() == current
    assert _digest(ast.dump(_run_node(current), include_attributes=True).encode()) == seventh.RUN_752_AST_SHA
    fidelity._validated_run_code(current, runner.run)
    asset = Path(seventh.__file__).parent / "fixtures/two_site_runner_752_source_inverse.json"
    raw = asset.read_bytes()
    assert len(raw) == seventh.ASSET_BYTES and _digest(raw) == seventh.ASSET_SHA
    for hunk in json.loads(raw)["hunks"]:
        before, after = hunk["before"].encode(), hunk["after"].encode()
        assert old.count(before) == current.count(after) == 1
        for partial in (old.replace(before, after, 1), current.replace(after, before, 1)):
            assert _digest(partial) not in fidelity._RUNNER_IDENTITIES
            with pytest.raises(AssertionError, match="unknown or partial"):
                seventh.apply_generation_completion_transport(partial.decode())
            with pytest.raises(fidelity.PromptFidelityError, match="SDK source pin mismatch"):
                fidelity._validated_run_code(partial, runner.run)
    with pytest.raises(AssertionError, match="unknown or partial"):
        seventh.apply_generation_completion_transport(current.decode() + "\n", inverse=True)
