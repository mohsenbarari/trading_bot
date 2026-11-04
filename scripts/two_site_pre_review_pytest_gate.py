"""Fail-closed pre-review pytest gate for zero genuinely passed tests."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import stat
import sys
import types
from pathlib import Path

import pytest
from _pytest.runner import CallInfo
from _pytest.reports import CollectReport, TestReport
from _pytest._code.code import ExceptionInfo

_passed_calls = 0
_records = []
_partial = False
_canonical_classes = []
_FIFO_ENV = "TWO_SITE_PYTEST_DIAGNOSTICS_FIFO"
_LIMIT = 8192
_SOURCE_LIMIT = 40000
_ROOT = Path(__file__).resolve().parents[1]
_NODEID = re.compile(r"tests/test[A-Za-z0-9_/-]*\.py(?:::[A-Za-z0-9_]+)*\Z")
_BUILTINS = tuple((cls, "builtins." + cls.__name__) for cls in (
    AssertionError, ValueError, TypeError, RuntimeError, LookupError, KeyError,
    IndexError, OSError, ImportError, ModuleNotFoundError, NameError,
    ZeroDivisionError, AttributeError, SyntaxError))
_AST = (ast.Module, ast.Assert, ast.Compare, ast.Name, ast.Load, ast.Constant,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot,
        ast.In, ast.NotIn, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
        ast.USub, ast.UAdd, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
        ast.FloorDiv, ast.Mod)


def _text(value, limit):
    if type(value) is not str or len(value) > limit:
        return False
    size = 0
    for character in value:
        point = ord(character)
        if 0xD800 <= point <= 0xDFFF:
            return False
        size += 1 if point < 128 else 2 if point < 2048 else 3 if point < 65536 else 4
        if size > limit:
            return False
    return True


def _json_size(value):
    if value is None:
        return 4
    if type(value) is bool:
        return 4 if value else 5
    if type(value) is int and value == 1:
        return 1
    if type(value) is str and _text(value, 512):
        size = 2
        for character in value:
            point = ord(character)
            size += (2 if character in '\\"\b\f\n\r\t' else 6 if point < 32
                     else 1 if point < 128 else 2 if point < 2048 else 3 if point < 65536 else 4)
        return size
    if type(value) is list and len(value) <= 16:
        return 2 + max(0, len(value) - 1) + sum(_json_size(item) for item in value)
    if type(value) is dict and len(value) <= 6:
        return 2 + max(0, len(value) - 1) + sum(_json_size(key) + 1 + _json_size(item) for key, item in value.items())
    raise ValueError("unsupported diagnostic primitive")


def _fields(obj):
    fields = object.__getattribute__(obj, "__dict__")
    if type(fields) is dict and len(fields) <= 32 and all(_text(key, 64) for key in fields):
        return fields
    return None


def _exception_identity(info):
    fields = _fields(info)
    raw = fields.get("_excinfo") if fields is not None else None
    if type(raw) is not tuple or len(raw) != 3:
        return None, True, None
    cls, value, traceback = raw
    for known, label in _BUILTINS:
        if cls is known and type(value) is known:
            return label, False, traceback
    if type(cls) is not type or type(value) is not cls:
        return None, True, None
    for known, name in _canonical_classes:
        if cls is not known:
            continue
        label = name + ".AutopilotError"
        args = BaseException.args.__get__(value, BaseException)
        message = "autopilot requires branch refactor/two-site-architecture"
        if type(args) is tuple and len(args) == 1 and _text(args[0], 160) and args[0] == message:
            label += ": " + message
        return label, not (type(args) is tuple and len(args) == 1 and _text(args[0], 160)), traceback
    return None, True, None


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


# Fixed source-syntax observations only: never inspect Mock instances or exception text.
_MOCK_ASSERTIONS = tuple(
    "UNITTEST_MOCK_TYPE_V1:" + method + "(type(_)," + cls +
    ");if_unittest_mock_use_isinstance_and_check_sync_async"
    for method in ("assertIs", "assertEqual") for cls in ("MagicMock", "AsyncMock")
)

_MOCK_HASATTR_ASSERTION = 'UNITTEST_MOCK_HASATTR_V1:assertFalse(hasattr(_,await_count));if_unittest_mock_check_isinstance_AsyncMock_and_iscoroutinefunction'

def _mock_type_assertion(node):
    if type(node) is not ast.Expr or type(node.value) is not ast.Call:
        return None
    call = node.value
    if (type(call.func) is ast.Attribute and type(call.func.value) is ast.Name
            and call.func.value.id == "self" and call.func.attr == "assertFalse"
            and len(call.args) == 1 and not call.keywords):
        actual = call.args[0]
        if (type(actual) is ast.Call and type(actual.func) is ast.Name and actual.func.id == "hasattr"
                and len(actual.args) == 2 and not actual.keywords and type(actual.args[0]) is ast.Name
                and type(actual.args[1]) is ast.Constant and type(actual.args[1].value) is str
                and actual.args[1].value == "await_count"):
            return _MOCK_HASATTR_ASSERTION
    if (type(call.func) is not ast.Attribute or type(call.func.value) is not ast.Name
            or call.func.value.id != "self" or call.func.attr not in ("assertIs", "assertEqual")
            or len(call.args) != 2 or call.keywords):
        return None
    actual, expected = call.args
    if (type(actual) is not ast.Call or type(actual.func) is not ast.Name
            or actual.func.id != "type" or len(actual.args) != 1 or actual.keywords
            or type(actual.args[0]) is not ast.Name or type(expected) is not ast.Name
            or expected.id not in ("MagicMock", "AsyncMock")):
        return None
    return ("UNITTEST_MOCK_TYPE_V1:" + call.func.attr + "(type(_)," + expected.id +
            ");if_unittest_mock_use_isinstance_and_check_sync_async")

def _source_assertion(traceback):
    candidate = None
    for _ in range(64):
        if traceback is None:
            break
        if type(traceback) is not types.TracebackType:
            return None
        filename = traceback.tb_frame.f_code.co_filename
        if _text(filename, 1024):
            path = Path(filename)
            if not path.is_absolute():
                path = _ROOT / path
            if path.is_relative_to(_ROOT / "tests"):
                candidate = path, traceback.tb_lineno
        traceback = traceback.tb_next
    else:
        return None
    if candidate is None:
        return None
    path, line = candidate
    descriptor = None
    try:
        if path.resolve() != path or path.is_symlink():
            return None
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened = os.fstat(descriptor)
        if _fingerprint(before) != _fingerprint(opened):
            return None
        data = bytearray()
        while len(data) <= _SOURCE_LIMIT:
            chunk = os.read(descriptor, min(4096, _SOURCE_LIMIT + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if (_fingerprint(opened) != _fingerprint(os.fstat(descriptor))
                or _fingerprint(opened) != _fingerprint(path.lstat()) or path.resolve() != path
                or len(data) > _SOURCE_LIMIT):
            return None
        tree = ast.parse(data.decode("utf-8"))
        for index, node in enumerate(ast.walk(tree)):
            if index > 2048:
                return None
            if type(node) is ast.Expr and node.lineno <= line <= node.end_lineno:
                shape = _mock_type_assertion(node)
                if shape is not None:
                    return shape  # Nodeid/proposal bind location; omit spelling and line from cause identity.
            if type(node) is ast.Assert and node.lineno <= line <= node.end_lineno:
                node.msg = None  # Never retain an assertion-message payload.
                result = ast.Module(body=[node], type_ignores=[])
                for count, item in enumerate(ast.walk(result)):
                    if count > 32 or type(item) not in _AST:
                        return None
                    if type(item) is ast.Name:
                        item.id = "_"
                    if type(item) is ast.Constant:
                        if type(item.value) not in (int, bool, type(None)):
                            return None
                        if type(item.value) is int and abs(item.value) > 1_000_000:
                            return None
                shape = ast.dump(result, annotate_fields=True, include_attributes=False)
                return shape if _text(shape, 512) else None
        return None
    except (OSError, ValueError, UnicodeError, SyntaxError, RecursionError):
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                return None


def _artifact():
    return {"version": 1, "records": _records, "partial": _partial}


def reset_gate() -> None:
    global _passed_calls, _records, _partial
    _passed_calls = 0
    _records = []
    _partial = False


def pytest_sessionstart(session: pytest.Session) -> None:
    reset_gate()


def _capture_canonical_classes():
    if os.environ.get(_FIFO_ENV) is None:
        return
    _canonical_classes.clear()
    source = _ROOT / "scripts/two_site_codex_autopilot.py"
    if not source.is_file() or source.is_symlink() or source.resolve() != source or source.stat().st_nlink != 1:
        return
    for name in ("scripts.two_site_codex_autopilot", "two_site_codex_autopilot"):
        try:
            if name.startswith("scripts."):
                module = importlib.import_module(name)
            else:
                spec = importlib.util.spec_from_file_location(name, source)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
            cls = object.__getattribute__(module, "__dict__")["AutopilotError"]
            if type(cls) is type:
                _canonical_classes.append((cls, name))
        except Exception:
            continue


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    global _passed_calls
    if (
        getattr(report, "when", None) == "call"
        and getattr(report, "passed", False)
        and not hasattr(report, "wasxfail")
    ):
        _passed_calls += 1


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _passed_calls
    if _passed_calls == 0 and getattr(session, "exitstatus", exitstatus) == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_exception_interact(node, call, report):
    global _partial
    if os.environ.get(_FIFO_ENV) is None:
        return
    if type(call) is not CallInfo or (type(report) is not TestReport and type(report) is not CollectReport):
        _partial = True
        return
    fields, calls = _fields(report), _fields(call)
    if fields is None or calls is None:
        _partial = True
        return
    phase = calls.get("when")
    if (type(phase) is not str or phase not in ("call", "setup", "teardown", "collect")
            or type(fields.get("outcome")) is not str or fields["outcome"] != "failed"
            or (phase == "collect") != (type(report) is CollectReport)):
        _partial = True
        return
    nodeid = fields.get("nodeid")
    if (not _text(nodeid, 512) or not _NODEID.fullmatch(nodeid)
            or any(part in ("", ".", "..") for part in nodeid.split("::", 1)[0].split("/"))):
        _partial = True
        return
    info = calls.get("excinfo")
    if type(info) is not ExceptionInfo:
        _partial = True
        return
    exception, partial, traceback = _exception_identity(info)
    assertion = None
    if exception == "builtins.AssertionError":
        assertion = _source_assertion(traceback)
        partial = assertion is None
    record = {"nodeid": nodeid, "phase": phase, "outcome": "failed",
              "exception": exception, "assertion": assertion, "partial": partial}
    try:
        size = _json_size(_artifact()) + _json_size(record) + (1 if _records else 0)
        if len(_records) >= 16 or size > _LIMIT - 1:
            _partial = True
            return
    except ValueError:
        _partial = True
        return
    _records.append(record)
    _partial = _partial or partial


@pytest.hookimpl(specname="pytest_sessionfinish", trylast=True)
def pytest_diagnostic_sessionfinish(session, exitstatus):
    path = os.environ.get(_FIFO_ENV)
    if path is None:
        return
    descriptor = None
    try:
        if not _text(path, 1024) or not Path(path).is_absolute():
            return
        payload = _artifact()
        if _json_size(payload) > _LIMIT:
            return
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        before = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISFIFO(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_uid != os.geteuid() or before.st_nlink != 1):
            return
        descriptor = os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        if _fingerprint(before) != _fingerprint(os.fstat(descriptor)):
            return
        os.write(descriptor, data)  # NONBLOCK: a partial write remains visibly invalid.
        if _fingerprint(before) != _fingerprint(os.stat(path, follow_symlinks=False)):
            return
    except (OSError, ValueError, UnicodeError, RecursionError):
        return
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


# Explicit -p plugin import precedes initial conftest and test collection.
_capture_canonical_classes()
