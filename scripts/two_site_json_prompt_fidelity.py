"""Prospective Root-only JSON fidelity; this module grants no execution authority."""
from __future__ import annotations

import __future__
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from types import CodeType, FunctionType
import unicodedata

RUNNER_SOURCE_SHA256 = "55cd2cc5f0dc8489514db8843d3e3114532d2beced0566b0456e2cf87d5c8af4"
RUN_AST_SHA256 = "90afa2143a2172212027a01092aa80107cd1b5f19cc6bdf3349671c9add0232b"
_RUNNER_IDENTITIES = {
    RUNNER_SOURCE_SHA256: RUN_AST_SHA256,
    "d786e58266893481c9bbd8447d0228170b2037f91073ac2b0ad447bc1f59367f":
        "8951f6c504fdfeeaaa047340f4b5557b581514e7d25da2bcafb938d42fa3197d",
    "cb5f6fa91ce195e6dbd5dffa73183ddbe440e05829bbbdd100c9dfef015b7bf6":
        "9785eb03a32ac1f13cd6ea197e96b64101eb6c1bfa4d3a7859052a3508dfea94",
    "502b26fb4528349d8bf7d3c9c6f7b45eca59e681cb5d05cf71ed88847b7c1f52":
        "ccc7d3403a6f9ae8a1738a0ef558ff47af8358b0296a286124603d3dd98c06ad",
}
_CONTEXT_CALL_LINES = {
    RUN_AST_SHA256: 3190,
    "8951f6c504fdfeeaaa047340f4b5557b581514e7d25da2bcafb938d42fa3197d": 3190,
    "9785eb03a32ac1f13cd6ea197e96b64101eb6c1bfa4d3a7859052a3508dfea94": 3237,
    "ccc7d3403a6f9ae8a1738a0ef558ff47af8358b0296a286124603d3dd98c06ad": 3237,
}
_ENCODER_NAME = "_root_review_context_json_720f"
_ROOT = Path(__file__).absolute().parents[1]
_RUNNER_PATH = _ROOT / "scripts/two_site_proposal_runner.py"
_SOURCE_LIMIT = 200000


class PromptFidelityError(ValueError):
    """Closed fixed-source failure; no source content is included in errors."""


def _need(condition, message):
    if not condition:
        raise PromptFidelityError(message)


def _require_json_equivalence(original, encoded):
    # Canonicalize decoded values to handle standard-library NaN/Infinity too.
    before, after = json.loads(original), json.loads(encoded)
    _need(json.dumps(before, ensure_ascii=False) == json.dumps(after, ensure_ascii=False),
          "decoded JSON changed")


def encode_context_json(value, *, ensure_ascii=False):
    """Serialize exactly as SDK CONTEXT, then JSON-escape only raw category Cf."""
    _need(ensure_ascii is False, "fixed ensure_ascii=False contract required")
    original = json.dumps(value, ensure_ascii=False)
    original.encode("utf-8", errors="strict")
    pieces = []
    for character in original:
        if unicodedata.category(character) != "Cf":
            pieces.append(character)
            continue
        point = ord(character)
        if point <= 0xFFFF:
            pieces.append("\\u%04x" % point)
        else:
            point -= 0x10000
            pieces.append("\\u%04x\\u%04x" % (0xD800 + (point >> 10), 0xDC00 + (point & 0x3FF)))
    encoded = "".join(pieces)
    encoded.encode("utf-8", errors="strict")
    _require_json_equivalence(original, encoded)
    _need(not any(unicodedata.category(c) == "Cf" for c in encoded), "raw format character remains")
    return encoded


def _read_runner_source():
    """Read only the one fixed SDK source with bounded descriptor custody."""
    path = _RUNNER_PATH
    _need(path.resolve(strict=True) == path, "SDK path alias")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
              and before.st_uid == os.geteuid() and before.st_size <= _SOURCE_LIMIT,
              "unsafe SDK source")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            body = stream.read(_SOURCE_LIMIT + 1)
        after, named = os.fstat(descriptor), path.lstat()
        fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        _need(len(body) <= _SOURCE_LIMIT and fingerprint(before) == fingerprint(after) == fingerprint(named)
              and path.resolve(strict=True) == path, "SDK source changed during read")
    finally:
        os.close(descriptor)
    _need(hashlib.sha256(body).hexdigest() in _RUNNER_IDENTITIES, "SDK source pin mismatch")
    return body


def _replace_context_call(run_node):
    """Transform exactly one pinned call; retain every other AST attribute."""
    _need(isinstance(run_node, ast.FunctionDef) and run_node.name == "run", "wrong SDK function AST")
    original_dump = ast.dump(run_node, include_attributes=True)
    run_ast_sha256 = hashlib.sha256(original_dump.encode()).hexdigest()
    _need(run_ast_sha256 in _RUNNER_IDENTITIES.values() and run_ast_sha256 in _CONTEXT_CALL_LINES,
          "complete SDK run AST pin mismatch")
    transformed = copy.deepcopy(run_node)
    matches = [node for node in ast.walk(transformed) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
               and node.func.value.id == "json" and node.func.attr == "dumps"
               and any(isinstance(arg, ast.Name) and arg.id == "prompt_material" for arg in node.args)]
    _need(len(matches) == 1, "CONTEXT JSON call cardinality drift")
    target = matches[0]
    expected = ast.parse("json.dumps(prompt_material, ensure_ascii=False)", mode="eval").body
    _need(target.lineno == _CONTEXT_CALL_LINES[run_ast_sha256]
          and ast.dump(target) == ast.dump(expected), "CONTEXT JSON call shape drift")
    old_function = copy.deepcopy(target.func)
    target.func = ast.copy_location(ast.Name(id=_ENCODER_NAME, ctx=ast.Load()), target.func)
    changed_dump = ast.dump(transformed, include_attributes=True)
    target.func = old_function
    _need(ast.dump(transformed, include_attributes=True) == original_dump, "unrelated SDK AST change")
    target.func = ast.copy_location(ast.Name(id=_ENCODER_NAME, ctx=ast.Load()), old_function)
    _need(ast.dump(transformed, include_attributes=True) == changed_dump, "fixed AST reconstruction mismatch")
    return transformed


def _compile_run(module_node):
    # Compile the complete module without executing it. Module import symbols
    # affect Python's call opcodes, so compiling an isolated def is insufficient.
    compiled = compile(module_node, str(_RUNNER_PATH), "exec",
                       flags=__future__.annotations.compiler_flag, dont_inherit=True, optimize=0)
    codes = [value for value in compiled.co_consts if isinstance(value, CodeType) and value.co_name == "run"]
    _need(len(codes) == 1, "compiled SDK function cardinality drift")
    return codes[0]


def _same_code_identity(actual, expected):
    """Compare code semantics plus recursive source/debug identity, not marshal."""
    if type(actual) is not CodeType or type(expected) is not CodeType or actual != expected:
        return False
    for name in ("co_filename", "co_firstlineno", "co_linetable", "co_exceptiontable", "co_qualname", "co_stacksize"):
        if getattr(actual, name) != getattr(expected, name):
            return False
    for left, right in zip(actual.co_consts, expected.co_consts):
        if isinstance(left, CodeType) or isinstance(right, CodeType):
            if not _same_code_identity(left, right):
                return False
    return True


def _validated_run_code(source, original):
    _need(type(source) is bytes and len(source) <= _SOURCE_LIMIT
          and hashlib.sha256(source).hexdigest() in _RUNNER_IDENTITIES, "SDK source pin mismatch")
    _need(type(original) is FunctionType and original.__module__ == "scripts.two_site_proposal_runner"
          and original.__name__ == original.__qualname__ == "run"
          and original.__code__.co_filename == str(_RUNNER_PATH) and original.__closure__ is None,
          "actual SDK run identity mismatch")
    tree = ast.parse(source.decode("utf-8", errors="strict"), filename=str(_RUNNER_PATH))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run"]
    _need(len(nodes) == 1, "SDK function source cardinality drift")
    node = nodes[0]
    _need(hashlib.sha256(ast.dump(node, include_attributes=True).encode()).hexdigest()
          == _RUNNER_IDENTITIES[hashlib.sha256(source).hexdigest()],
          "paired SDK source/run AST pin mismatch")
    expected_code = _compile_run(tree)
    _need(_same_code_identity(original.__code__, expected_code), "actual SDK run code mismatch")
    transformed = copy.deepcopy(tree)
    transformed.body[tree.body.index(node)] = _replace_context_call(node)
    return _compile_run(transformed)


def make_root_review_run(prior_loader):
    """Return a private fixed verifier function; never invoke it or change SDK."""
    from scripts import two_site_proposal_runner as runner

    _need(sys.flags.optimize == 0, "nonoptimized interpreter required")
    _need(type(prior_loader) is FunctionType and prior_loader.__name__ == "prior_loader",
          "existing closed prior_loader function required")
    _need(runner is sys.modules.get("scripts.two_site_proposal_runner")
          and runner.__name__ == "scripts.two_site_proposal_runner"
          and Path(runner.__file__).absolute() == _RUNNER_PATH and runner.ROOT == _ROOT,
          "actual SDK module identity mismatch")
    before = dict(vars(runner))
    original = runner.run
    _need(original.__globals__ is vars(runner) and _ENCODER_NAME not in before,
          "SDK namespace identity or fixed binding collision")
    _need(original.__defaults__ == (None, None, None, None)
          and original.__kwdefaults__ == {"pre_provider_check": None, "fallback_from_run_id": None, "retry_hint": None},
          "SDK default argument drift")
    source = _read_runner_source()
    code = _validated_run_code(source, original)
    namespace = dict(before)
    namespace["load_prior_worker"] = prior_loader
    namespace[_ENCODER_NAME] = encode_context_json
    _need(set(namespace) - set(before) == {_ENCODER_NAME}
          and {name for name in before if namespace[name] is not before[name]} == {"load_prior_worker"},
          "private namespace changed outside two fixed bindings")
    private = FunctionType(code, namespace, original.__name__, original.__defaults__, original.__closure__)
    private.__kwdefaults__ = dict(original.__kwdefaults__)
    private.__annotations__ = dict(original.__annotations__)
    private.__qualname__ = original.__qualname__
    private.__dict__.update(original.__dict__)
    _need(set(vars(runner)) == set(before) and all(vars(runner)[name] is value for name, value in before.items())
          and runner.run is original and _read_runner_source() == source, "shared SDK or source changed")
    return private
