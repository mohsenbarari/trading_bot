"""Host-controller test runner: disposable source, empty credentials, no network.

Not an arbitrary shell gateway. Only explicit pytest selectors are accepted; the
caller has already bound its operation to an Assignment/fence and actor role.
"""
from __future__ import annotations

import ast
import fcntl
import hashlib
import json
import os
import re
import selectors as io_selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import zlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence


class SandboxError(ValueError):
  pass


SELECTOR = re.compile(r"tests/test[A-Za-z0-9_./-]*\.py(?:::[A-Za-z0-9_]+)*\Z")
EXCLUDED_ROOTS = {".git", ".local", ".venv", "venv", "node_modules", "logs", "log",
         "tmp", "temp", "backups", "backup", "state", "sessions", "uploads", "media"}
SOURCE_SUFFIXES = {".py", ".pyi", ".md", ".json", ".yaml", ".yml", ".toml", ".ini",
         ".cfg", ".txt", ".sql", ".sh", ".html", ".css", ".js", ".ts",
         ".tsx", ".jsx", ".vue", ".csv", ".feature", ".example"}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
MAX_OUTPUT_BYTES = 8_000_000
MAX_ANCESTRY_COMMITS = 4096
MAX_ANCESTRY_BYTES = 8_000_000
READ_CHUNK = 64 * 1024
PROCESS_CLEANUP_GRACE = 2.0
DEADLINE_CLEANUP_RESERVE = 2 * PROCESS_CLEANUP_GRACE + 1.0
PRE_STOP_DEADLINE_SECONDS = 600
POST_DEADLINE_SECONDS = 2280
HOOK_TIMEOUT_MARGIN_SECONDS = 120
RECOVERY_DEADLINE_SECONDS = 600
MAX_TEST_MEMORY_BYTES = 4 * 1024 * 1024 * 1024
MAX_TEST_PROCESSES = 128
MAX_TEST_TMP_BYTES = 512 * 1024 * 1024
MAX_TEST_FILE_BYTES = 128 * 1024 * 1024
MAX_TEST_OPEN_FILES = 512
MAX_TEST_CPU_SECONDS = 1200
DIAGNOSTIC_LIMIT = 8192
DIAGNOSTIC_CHUNK = 4096
DIAGNOSTIC_PATH = "/tmp/two-site-pytest-diagnostics.fifo"
DIAGNOSTIC_ENV = "TWO_SITE_PYTEST_DIAGNOSTICS_FIFO"
_EXCEPTIONS = {"builtins." + name for name in (
  "AssertionError", "ValueError", "TypeError", "RuntimeError", "LookupError",
  "KeyError", "IndexError", "OSError", "ImportError", "ModuleNotFoundError",
  "NameError", "ZeroDivisionError", "AttributeError", "SyntaxError")}
for _module in ("scripts.two_site_codex_autopilot", "two_site_codex_autopilot"):
  _identity = _module + ".AutopilotError"
  _EXCEPTIONS.update((_identity, _identity + ": autopilot requires branch refactor/two-site-architecture"))
_NODEID = re.compile(r"tests/test[A-Za-z0-9_/-]*\.py(?:::[A-Za-z0-9_]+)*\Z")
_AST_TYPES = {name: getattr(ast, name) for name in (
  "Module", "Assert", "Compare", "Name", "Load", "Constant", "Eq", "NotEq",
  "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn", "BoolOp",
  "And", "Or", "UnaryOp", "Not", "USub", "UAdd", "BinOp", "Add", "Sub",
  "Mult", "Div", "FloorDiv", "Mod")}


def _bounded_text(value, limit):
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


def _assertion_shape(value):
  if not _bounded_text(value, 512):
    return False
  try:
    expression = ast.parse(value, mode="eval")
    count = 0

    def construct(node):
      nonlocal count
      count += 1
      if count > 96:
        raise ValueError
      if type(node) is ast.Constant and type(node.value) in (str, int, bool, type(None)):
        if type(node.value) is str and node.value != "_":
          raise ValueError
        if type(node.value) is int and abs(node.value) > 1_000_000:
          raise ValueError
        return node.value
      if type(node) is ast.List:
        return [construct(item) for item in node.elts]
      if type(node) is not ast.Call or type(node.func) is not ast.Name or node.args:
        raise ValueError
      cls = _AST_TYPES.get(node.func.id)
      expected = ("value",) if cls is ast.Constant else ("test",) if cls is ast.Assert else (() if cls is None else cls._fields)
      if cls is None or len(node.keywords) != len(expected):
        raise ValueError
      fields = {}
      for keyword in node.keywords:
        if keyword.arg not in expected or keyword.arg in fields:
          raise ValueError
        fields[keyword.arg] = construct(keyword.value)
      if cls is ast.Assert:
        fields["msg"] = None
      return cls(**fields)

    tree = construct(expression.body)
    if (type(tree) is not ast.Module or type(tree.body) is not list or len(tree.body) != 1
        or type(tree.body[0]) is not ast.Assert or tree.body[0].msg is not None
        or tree.type_ignores != []):
      return False
    expressions = (ast.Name, ast.Constant, ast.Compare, ast.BoolOp, ast.UnaryOp, ast.BinOp)
    def expression_valid(node):
      if type(node) not in expressions:
        return False
      if type(node) is ast.Compare:
        return (type(node.ops) is list and type(node.comparators) is list
            and 0 < len(node.ops) == len(node.comparators)
            and all(type(op) in (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn) for op in node.ops)
            and expression_valid(node.left) and all(expression_valid(item) for item in node.comparators))
      if type(node) is ast.BoolOp:
        return type(node.op) in (ast.And, ast.Or) and type(node.values) is list and len(node.values) >= 2 and all(expression_valid(item) for item in node.values)
      if type(node) is ast.UnaryOp:
        return type(node.op) in (ast.Not, ast.USub, ast.UAdd) and expression_valid(node.operand)
      if type(node) is ast.BinOp:
        return type(node.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod) and expression_valid(node.left) and expression_valid(node.right)
      return True
    if not expression_valid(tree.body[0].test):
      return False
    for node in ast.walk(tree):
      if type(node) not in tuple(_AST_TYPES.values()):
        return False
      if type(node) is ast.Name and (node.id != "_" or type(node.ctx) is not ast.Load):
        return False
      if type(node) is ast.Constant:
        if type(node.value) not in (int, bool, type(None)):
          return False
        if type(node.value) is int and abs(node.value) > 1_000_000:
          return False
    return ast.dump(tree, annotate_fields=True, include_attributes=False) == value
  except (SyntaxError, ValueError, TypeError, RecursionError):
    return False


def _diagnostic_artifact(data):
  if type(data) is not bytes or not data or len(data) > DIAGNOSTIC_LIMIT:
    raise SandboxError("diagnostic unavailable or oversized")

  def unique(pairs):
    result = {}
    for key, value in pairs:
      if key in result:
        raise ValueError
      result[key] = value
    return result

  try:
    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    if (type(value) is not dict or set(value) != {"version", "records", "partial"}
        or type(value["version"]) is not int or value["version"] != 1
        or type(value["partial"]) is not bool or type(value["records"]) is not list
        or len(value["records"]) > 16):
      raise ValueError
    for record in value["records"]:
      if type(record) is not dict or set(record) != {"nodeid", "phase", "outcome", "exception", "assertion", "partial"}:
        raise ValueError
      nodeid, phase = record["nodeid"], record["phase"]
      if not _bounded_text(nodeid, 512) or not _NODEID.fullmatch(nodeid):
        raise ValueError
      safe_relative(nodeid.split("::", 1)[0])
      if (type(phase) is not str or phase not in ("call", "setup", "teardown", "collect")
          or type(record["outcome"]) is not str or record["outcome"] != "failed"
          or type(record["partial"]) is not bool):
        raise ValueError
      exception, assertion = record["exception"], record["assertion"]
      if exception is not None and (not _bounded_text(exception, 160) or exception not in _EXCEPTIONS):
        raise ValueError
      if assertion is not None and not _assertion_shape(assertion):
        raise ValueError
      if (exception is None or record["partial"]) and not value["partial"]:
        raise ValueError
      if exception is None and not record["partial"]:
        raise ValueError
    return value
  except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
    raise SandboxError("diagnostic invalid") from exc


class _DiagnosticFIFO:

  def __init__(self):
    self.directory = Path(tempfile.mkdtemp(prefix="two-site-pytest-fifo-"))
    self.path = self.directory / "report.fifo"
    self.parent_fd = self.reader = self.keeper = None
    self.identity = self.parent_identity = None
    self.data = bytearray()
    self.error = None
    self.artifact = None
    self.capacity_ready = False
    try:
      self.parent_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
      self.parent_identity = os.fstat(self.parent_fd)
      os.mkfifo("report.fifo", 0o600, dir_fd=self.parent_fd)
      self.identity = os.stat("report.fifo", dir_fd=self.parent_fd, follow_symlinks=False)
      self.check()
      self.reader = os.open("report.fifo", os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.parent_fd)
      self.check()
      self.keeper = os.open("report.fifo", os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.parent_fd)
      self.check()
      fcntl.fcntl(self.reader, fcntl.F_SETPIPE_SZ, DIAGNOSTIC_LIMIT)
      for descriptor in (self.reader, self.keeper):
        if not 0 < fcntl.fcntl(descriptor, fcntl.F_GETPIPE_SZ) <= DIAGNOSTIC_LIMIT:
          raise SandboxError("diagnostic capacity invalid")
      self.capacity_ready = True
      self.check()
    except BaseException as exc:
      self.close()
      if self.error is not None:
        exc.add_note("diagnostic cleanup failure")
      raise

  @staticmethod
  def _same(left, right):
    return (left.st_dev, left.st_ino, left.st_mode, left.st_uid, left.st_nlink) == (right.st_dev, right.st_ino, right.st_mode, right.st_uid, right.st_nlink)

  def check(self):
    try:
      parent = os.fstat(self.parent_fd)
      path_parent = self.directory.lstat()
      if (not self._same(parent, self.parent_identity) or not self._same(path_parent, parent)
          or not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700
          or parent.st_uid != os.geteuid() or self.directory.resolve() != self.directory):
        raise SandboxError("diagnostic parent custody failure")
      current = os.stat("report.fifo", dir_fd=self.parent_fd, follow_symlinks=False)
      with os.scandir(self.parent_fd) as entries:
        first = next(entries, None)
        if first is None or first.name != "report.fifo" or next(entries, None) is not None:
          raise SandboxError("diagnostic directory custody failure")
      if (not self._same(current, self.identity) or not stat.S_ISFIFO(current.st_mode)
          or stat.S_IMODE(current.st_mode) != 0o600 or current.st_uid != os.geteuid()
          or current.st_nlink != 1):
        raise SandboxError("diagnostic FIFO custody failure")
      for access, descriptor in enumerate((self.reader, self.keeper)):
        if descriptor is not None:
          flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
          if (not self._same(os.fstat(descriptor), current) or os.get_inheritable(descriptor)
              or not flags & os.O_NONBLOCK or flags & os.O_ACCMODE != access
              or self.capacity_ready and not 0 < fcntl.fcntl(descriptor, fcntl.F_GETPIPE_SZ) <= DIAGNOSTIC_LIMIT):
            raise SandboxError("diagnostic descriptor custody failure")
    except OSError as exc:
      raise SandboxError("diagnostic custody unavailable") from exc

  def drain(self):
    self.check()
    try:
      chunk = os.read(self.reader, DIAGNOSTIC_CHUNK)
    except BlockingIOError:
      chunk = b""
    if chunk:
      room = DIAGNOSTIC_LIMIT + 1 - len(self.data)
      self.data.extend(chunk[:room])
      if len(self.data) > DIAGNOSTIC_LIMIT:
        self.error = "diagnostic overflow"
    self.check()

  def finish(self):
    try:
      self.check()
      for _ in range(3):
        self.drain()
      self.check()
      if self.error is None:
        self.artifact = _diagnostic_artifact(bytes(self.data))
    except (SandboxError, OSError):
      self.error = "diagnostic unavailable or invalid"
      self.artifact = None

  def close(self):
    try:
      if self.identity is not None:
        self.check()
    except (SandboxError, OSError, TypeError):
      self.error = "diagnostic custody failure"
      self.artifact = None
    for name in ("reader", "keeper"):
      descriptor = getattr(self, name)
      if descriptor is not None:
        try:
          os.close(descriptor)
        except OSError:
          self.error = "diagnostic cleanup failure"
          self.artifact = None
        setattr(self, name, None)
    try:
      if self.parent_fd is not None:
        current = os.stat("report.fifo", dir_fd=self.parent_fd, follow_symlinks=False)
        if self.identity is not None and self._same(current, self.identity):
          os.unlink("report.fifo", dir_fd=self.parent_fd)
        else:
          self.error = "diagnostic cleanup failure"
          self.artifact = None
    except FileNotFoundError:
      pass
    except OSError:
      self.error = "diagnostic cleanup failure"
      self.artifact = None
    try:
      if self.parent_identity is None or self._same(self.directory.lstat(), self.parent_identity):
        self.directory.rmdir()
      else:
        self.error = "diagnostic cleanup failure"
        self.artifact = None
    except OSError:
      self.error = "diagnostic cleanup failure"
      self.artifact = None
    finally:
      if self.parent_fd is not None:
        try:
          os.close(self.parent_fd)
        except OSError:
          self.error = "diagnostic cleanup failure"
          self.artifact = None
        self.parent_fd = None


def _diagnostic_argv(argv, channel):
  channel.check()
  index = argv.index("/usr/bin/python3")
  result = argv[:index] + ["--bind", str(channel.path), DIAGNOSTIC_PATH,
               "--setenv", DIAGNOSTIC_ENV, DIAGNOSTIC_PATH] + argv[index:]
  channel.check()
  return result


@dataclass(frozen=True)
class GitAncestryEvidence:
    """Exact trusted Git boundary to reproduce without exposing the host object DB."""

    ancestor_sha: str
    head_sha: str


_ACTIVE_DEADLINE: ContextVar[float | None] = ContextVar(
    "two_site_agent_end_to_end_deadline", default=None
)


class EndToEndDeadlineExceeded(SandboxError):
    """The protected hook exhausted its shared wall-clock budget."""


def hook_deadline_seconds(phase: str) -> int:
    if phase in {"pre", "stop"}:
        return PRE_STOP_DEADLINE_SECONDS
    if phase == "post":
        return POST_DEADLINE_SECONDS
    raise SandboxError("unknown protected hook phase")


def hook_timeout_seconds(phase: str) -> int:
    """Return the outer Antigravity timeout derived from the enforced deadline."""

    return hook_deadline_seconds(phase) + HOOK_TIMEOUT_MARGIN_SECONDS


def _deadline_timeout(requested: float) -> float:
    """Cap one child process while preserving time to kill all descendants."""

    deadline = _ACTIVE_DEADLINE.get()
    if deadline is None:
        return requested
    remaining = deadline - time.monotonic() - DEADLINE_CLEANUP_RESERVE
    if remaining <= 0:
        raise EndToEndDeadlineExceeded("protected operation exhausted its end-to-end deadline")
    return min(requested, remaining)


@contextmanager
def operation_deadline(seconds: float):
    """Own a main-thread deadline, reserving cleanup time and refusing other timers."""

    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        raise SandboxError("end-to-end deadline must be positive")
    if threading.current_thread() is not threading.main_thread():
        raise SandboxError("end-to-end deadline requires the protected main thread")
    previous_deadline = _ACTIVE_DEADLINE.get()
    if previous_deadline is not None:
        raise SandboxError("nested end-to-end deadlines are unsupported")
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer != (0.0, 0.0):
        raise SandboxError("pre-existing interval timer prevents deadline ownership")
    previous_handler = signal.getsignal(signal.SIGALRM)
    deadline = time.monotonic() + float(seconds)
    token = _ACTIVE_DEADLINE.set(deadline)

    def expired(_signum, _frame):
        raise EndToEndDeadlineExceeded("protected operation exhausted its end-to-end deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        _ACTIVE_DEADLINE.reset(token)


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate the isolated command and all descendants in its host process group."""

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    # Reap the leader before probing surviving descendants.
    try:
        process.wait(timeout=min(0.1, PROCESS_CLEANUP_GRACE))
    except subprocess.TimeoutExpired:
        pass
    deadline = time.monotonic() + PROCESS_CLEANUP_GRACE
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=PROCESS_CLEANUP_GRACE)
    except subprocess.TimeoutExpired as exc:
        raise SandboxError("isolated process group could not be cleaned up") from exc


def _run_bounded_process(
  argv: Sequence[str],
  *,
  cwd: Path | None = None,
  env: Mapping[str, str] | None = None,
  timeout: float,
  output_limit: int,
  memory_limit: int | None = None,
  process_limit: int | None = None,
  _diagnostics: _DiagnosticFIFO | None = None,
) -> subprocess.CompletedProcess[bytes]:
  """Capture stdout/stderr incrementally and kill the process group on any budget breach."""

  if not argv or any(not isinstance(value, str) or not value for value in argv):
    raise SandboxError("bounded process requires fixed non-empty argv")
  if (timeout <= 0 or output_limit <= 0
      or memory_limit is not None and memory_limit <= 0
      or process_limit is not None and process_limit <= 0):
    raise SandboxError("bounded process budgets must be positive")
  timeout = _deadline_timeout(timeout)
  if _diagnostics is not None:
    _diagnostics.check()
  process = subprocess.Popen(
    list(argv),
    cwd=str(cwd) if cwd is not None else None,
    env=dict(env) if env is not None else None,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    shell=False,
    start_new_session=True,
    close_fds=True,
  )
  selector = None
  buffers = {"stdout": bytearray(), "stderr": bytearray()}
  total = 0
  deadline = time.monotonic() + timeout
  primary = None
  try:
    assert process.stdout is not None and process.stderr is not None
    selector = io_selectors.DefaultSelector()
    selector.register(process.stdout, io_selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, io_selectors.EVENT_READ, "stderr")
    if _diagnostics is not None:
      _diagnostics.check()
      selector.register(_diagnostics.reader, io_selectors.EVENT_READ, "diagnostics")
    stdio_open = 2
    while stdio_open:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        raise SandboxError("isolated process exceeded its timeout")
      events = selector.select(min(remaining, 0.1))
      if memory_limit is not None or process_limit is not None:
        count, memory = _descendant_usage(process.pid)
        if process_limit is not None and count > process_limit:
          raise SandboxError("isolated process count exceeded its resource budget")
        if memory_limit is not None and memory > memory_limit:
          raise SandboxError("isolated process memory exceeded its resource budget")
      for key, _ in events:
        if key.data == "diagnostics":
          try:
            _diagnostics.drain()
          except (SandboxError, OSError):
            _diagnostics.error = "diagnostic custody or read failure"
            _diagnostics.artifact = None
            selector.unregister(key.fileobj)
          continue
        chunk = os.read(key.fd, READ_CHUNK)
        if not chunk:
          selector.unregister(key.fileobj)
          stdio_open -= 1
          continue
        if total + len(chunk) > output_limit:
          raise SandboxError("isolated process output exceeded its evidence budget")
        buffers[key.data].extend(chunk)
        total += len(chunk)
    returncode = process.wait(timeout=PROCESS_CLEANUP_GRACE)
    if _diagnostics is not None:
      if _diagnostics.reader in selector.get_map():
        selector.unregister(_diagnostics.reader)
      _diagnostics.finish()
    return subprocess.CompletedProcess(
      list(argv),
      returncode,
      bytes(buffers["stdout"]),
      bytes(buffers["stderr"]),
    )
  except BaseException as exc:
    primary = exc
    try:
      _stop_process_group(process)
    except BaseException as cleanup_error:
      if _diagnostics is None:
        if isinstance(cleanup_error, SandboxError):
          raise cleanup_error from exc
        raise
      exc.add_note("isolated process cleanup failure")
    if _diagnostics is not None and _diagnostics.error is not None:
      exc.add_note("diagnostic unavailable")
    raise
  finally:
    if _diagnostics is None:
      if selector is not None:
        selector.close()
      if process.stdout is not None:
        process.stdout.close()
      if process.stderr is not None:
        process.stderr.close()
    else:
      for resource in (selector, process.stdout, process.stderr):
        if resource is not None:
          try:
            resource.close()
          except BaseException:
            _diagnostics.error = "diagnostic process cleanup failure"
            _diagnostics.artifact = None
            if primary is not None:
              primary.add_note("diagnostic process cleanup failure")


def _descendant_usage(root_pid: int) -> tuple[int, int]:
    """Count the host-visible descendant tree and aggregate resident memory."""

    pending, seen, resident = [root_pid], set(), 0
    page = os.sysconf("SC_PAGE_SIZE")
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            values = Path(f"/proc/{pid}/statm").read_text(encoding="ascii").split()
            children = Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="ascii").split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, UnicodeError, ValueError) as exc:
            raise SandboxError("cannot account isolated process resources") from exc
        if len(values) < 2 or not values[1].isdigit() or any(not item.isdigit() for item in children):
            raise SandboxError("unknown isolated process accounting layout")
        resident += int(values[1]) * page
        pending.extend(int(item) for item in children)
    return len(seen), resident


def safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise SandboxError("invalid source path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise SandboxError("source path escapes the assigned worktree")
    return path


def selectors(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values or len(values) > 200:
        raise SandboxError("explicit bounded pytest selectors required")
    for value in values:
        if not isinstance(value, str) or not SELECTOR.fullmatch(value):
            raise SandboxError("unsupported pytest selector or option")
        safe_relative(value.split("::", 1)[0])
    if len(values) != len(set(values)):
        raise SandboxError("duplicate pytest selector")
    return values


def source_files(worktree: Path, extra_paths: list[str]) -> list[str]:
    result = _run_bounded_process(
        ["git", "-C", str(worktree), "ls-files", "-z"],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        timeout=30,
        output_limit=MAX_OUTPUT_BYTES,
    )
    if result.returncode != 0:
        raise SandboxError("trusted source inventory failed")
    try:
        candidates = set(result.stdout.decode("utf-8").split("\0")) - {""}
    except UnicodeDecodeError as exc:
        raise SandboxError("source inventory contains a non-UTF-8 path") from exc
    candidates.update(extra_paths)
    selected = []
    for value in sorted(candidates):
        path = safe_relative(value)
        if path.parts[0] in EXCLUDED_ROOTS or "__pycache__" in path.parts:
            continue
        if any(part.startswith(".env") and not part.endswith(".example") for part in path.parts):
            continue
        if any(part.split(".", 1)[0].lower() in {"credentials", "secrets", "private", "dumps"} for part in path.parts):
            continue
        if path.parts[0] == "data" or str(path).startswith("docs/memory/archive/"):
            continue
        if path.suffix not in SOURCE_SUFFIXES and path.name not in {"Makefile", "Dockerfile"}:
            continue
        original = worktree / path
        if not original.exists():  # A legitimate tracked deletion in the candidate.
            continue
        if original.is_symlink() or original.resolve() != original or not original.is_file():
            raise SandboxError("source snapshot refuses symlinks/nonfiles")
        if not original.resolve().is_relative_to(worktree):
            raise SandboxError("source file outside registered worktree")
        selected.append(value)
    return selected


def _sha40(value: str, label: str) -> str:
    if not isinstance(value, str) or not SHA40.fullmatch(value):
        raise SandboxError(f"{label} must be a full lowercase Git SHA")
    return value


def _git_read(worktree: Path, arguments: Sequence[str], *, output_limit: int) -> bytes:
    result = _run_bounded_process(
        ["git", "--no-replace-objects", "-C", str(worktree), *arguments],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        timeout=30,
        output_limit=output_limit,
    )
    if result.returncode != 0:
        raise SandboxError("trusted Git ancestry query failed")
    return result.stdout


def _write_loose_commit(git_dir: Path, object_id: str, body: bytes) -> int:
    canonical = b"commit " + str(len(body)).encode("ascii") + b"\0" + body
    try:
        observed = hashlib.sha1(canonical, usedforsecurity=False).hexdigest()
    except TypeError:  # pragma: no cover - compatibility for Python without this keyword.
        observed = hashlib.sha1(canonical).hexdigest()
    if observed != object_id:
        raise SandboxError("Git returned commit bytes that do not match their object ID")
    destination = git_dir / "objects" / object_id[:2] / object_id[2:]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(zlib.compress(canonical))
    return len(canonical)


def materialize_git_ancestry(
    worktree: Path,
    snapshot: Path,
    evidence: GitAncestryEvidence,
    *,
    byte_limit: int = MAX_ANCESTRY_BYTES,
) -> dict[str, int | str]:
    """Rehash trusted commits only; shallow boundaries never invent ancestry."""

    if not isinstance(byte_limit, int) or isinstance(byte_limit, bool) or byte_limit <= 0:
        raise SandboxError("Git ancestry byte budget must be positive")
    ancestor = _sha40(evidence.ancestor_sha, "ancestry ancestor")
    head = _sha40(evidence.head_sha, "ancestry head")
    actual_head = _git_read(worktree, ("rev-parse", "--verify", "HEAD^{commit}"), output_limit=128).decode().strip()
    if actual_head != head:
        raise SandboxError("trusted ancestry head differs from registered worktree HEAD")
    _git_read(worktree, ("merge-base", "--is-ancestor", ancestor, head), output_limit=128)

    if ancestor == head:
        object_ids = [head]
        boundary_ids = [head]
    else:
        raw = _git_read(worktree, ("rev-list", "--boundary", f"{ancestor}..{head}"), output_limit=MAX_ANCESTRY_COMMITS * 42)
        lines = raw.decode("ascii").splitlines()
        if not lines or len(lines) > MAX_ANCESTRY_COMMITS:
            raise SandboxError("Git ancestry slice exceeds the reviewed commit budget")
        object_ids = []
        boundary_ids = []
        for line in lines:
            boundary = line.startswith("-")
            object_id = _sha40(line[1:] if boundary else line, "ancestry object")
            if object_id not in object_ids:
                object_ids.append(object_id)
            if boundary and object_id not in boundary_ids:
                boundary_ids.append(object_id)
        if head not in object_ids or ancestor not in boundary_ids:
            raise SandboxError("Git ancestry slice lacks its exact head or ancestor boundary")

    git_dir = snapshot / ".git"
    if git_dir.exists():
        raise SandboxError("source snapshot unexpectedly contains Git metadata")
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "refs" / "heads").mkdir(parents=True)
    total = 0
    for object_id in object_ids:
        body = _git_read(worktree, ("cat-file", "commit", object_id), output_limit=min(byte_limit, 512_000))
        total += _write_loose_commit(git_dir, object_id, body)
        if total > byte_limit:
            raise SandboxError("Git ancestry objects exceed the reviewed byte budget")
    (git_dir / "HEAD").write_text("ref: refs/heads/sandbox\n", encoding="ascii")
    (git_dir / "refs" / "heads" / "sandbox").write_text(head + "\n", encoding="ascii")
    (git_dir / "shallow").write_text("".join(f"{value}\n" for value in sorted(boundary_ids)), encoding="ascii")
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = false\n\tbare = false\n",
        encoding="ascii",
    )
    return {
        "ancestor_sha": ancestor,
        "head_sha": head,
        "commit_objects": len(object_ids),
        "commit_object_bytes": total,
    }


def _awk_alternative_args() -> list[str]:
    """Recreate only the public awk alternatives link required by /usr/bin/awk."""

    awk = Path("/usr/bin/awk")
    if not awk.is_symlink():
        if not awk.is_file() or not os.access(awk, os.X_OK):
            raise SandboxError("host awk executable is unavailable")
        return []
    first_target = os.readlink(awk)
    if first_target != "/etc/alternatives/awk":
        raise SandboxError("/usr/bin/awk has an unsupported symlink target")
    alternative = Path(first_target)
    if not alternative.is_symlink():
        raise SandboxError("awk alternatives entry is unavailable")
    executable = os.readlink(alternative)
    if not executable.startswith("/usr/bin/"):
        raise SandboxError("awk alternative escapes the read-only /usr mount")
    resolved = Path(executable)
    if resolved.is_symlink() or not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise SandboxError("awk alternative is not a direct executable in /usr/bin")
    return ["--dir", "/etc/alternatives", "--symlink", executable, first_target]


def sandbox_argv(snapshot: Path, selected_tests: list[str], bwrap: str = "/usr/bin/bwrap") -> list[str]:
    tests = selectors(selected_tests)
    args = ["/usr/bin/prlimit", "--core=0", f"--as={MAX_TEST_MEMORY_BYTES}",
            f"--cpu={MAX_TEST_CPU_SECONDS}",
            f"--nproc={MAX_TEST_PROCESSES}", f"--nofile={MAX_TEST_OPEN_FILES}",
            f"--fsize={MAX_TEST_FILE_BYTES}", "--", bwrap,
            "--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent", "--new-session",
            "--uid", "65534", "--gid", "65534", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--proc", "/proc", "--dev", "/dev", "--symlink", "/proc/self/fd", "/dev/fd",
            "--symlink", "/proc/self/fd/0", "/dev/stdin", "--symlink", "/proc/self/fd/1", "/dev/stdout",
            "--symlink", "/proc/self/fd/2", "/dev/stderr",
            "--size", str(MAX_TEST_TMP_BYTES), "--tmpfs", "/tmp", "--chmod", "1777", "/tmp", "--dir", "/root",
            "--dir", "/etc", *_awk_alternative_args(), "--ro-bind", str(snapshot), "/work", "--chdir", "/work",
            "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
            "--setenv", "PYTHONPATH", "/work", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--setenv", "APP_ENV_FILE", "config/unit-test.env.example", "--setenv", "TMPDIR", "/tmp",
            "--setenv", "GIT_CONFIG_COUNT", "1", "--setenv", "GIT_CONFIG_KEY_0", "safe.directory",
            "--setenv", "GIT_CONFIG_VALUE_0", "/work",
            "--setenv", "LANG", "C.UTF-8"]
    for value in ("/etc/ld.so.cache", "/etc/localtime", "/etc/passwd", "/etc/group"):
        if Path(value).is_file():
            args += ["--ro-bind", value, value]
    return args + [
        "/usr/bin/python3",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "scripts.two_site_pre_review_pytest_gate",
        *tests,
    ]


def _validate_overlay_path(value: str) -> PurePosixPath:
    path = safe_relative(value)
    if str(path) != value:
        raise SandboxError("overlay path is not canonical")
    if path.parts[0] in EXCLUDED_ROOTS or "__pycache__" in path.parts:
        raise SandboxError("overlay path in excluded root")
    if any(part.startswith(".env") and not part.endswith(".example") for part in path.parts):
        raise SandboxError("overlay path exposes environment file")
    if any(part.split(".", 1)[0].lower() in {"credentials", "secrets", "private", "dumps"} for part in path.parts):
        raise SandboxError("overlay path exposes credential or private location")
    if path.parts[0] == "data" or str(path).startswith("docs/memory/archive/"):
        raise SandboxError("overlay path in restricted data/archive location")
    if path.suffix not in SOURCE_SUFFIXES and path.name not in {"Makefile", "Dockerfile"}:
        raise SandboxError("overlay path has unsupported suffix")
    return path


def run_tests(worktree: Path, selected_tests: list[str], extra_paths: list[str] | None = None,
       *, timeout: int = 1200, snapshot_limit: int = 300_000_000,
       git_ancestry: GitAncestryEvidence | None = None,
       overlays: Mapping[str, str] | None = None,
       structured_diagnostics: bool = False) -> dict:
  if type(structured_diagnostics) is not bool:
    raise SandboxError("structured_diagnostics must be a boolean")
  worktree = worktree.resolve(strict=True)
  selectors(selected_tests)
  if not 1 <= timeout <= 3600:
    raise SandboxError("test timeout outside controller budget")
  validated_overlays: dict[str, bytes] = {}
  if overlays is not None:
    if not isinstance(overlays, Mapping) or isinstance(overlays, (str, bytes)):
      raise SandboxError("overlays must be a mapping")
    if len(overlays) > 32:
      raise SandboxError("overlays mapping exceeds 32 entries")
    extra_set = {str(safe_relative(p)) for p in (extra_paths or [])}
    for key, content in overlays.items():
      path = _validate_overlay_path(key)
      if key in extra_set or str(path) in extra_set or key in (extra_paths or ()):
        raise SandboxError("overlay path overlaps extra_paths")
      if not isinstance(content, str):
        raise SandboxError("overlay content must be a string")
      if not content:
        raise SandboxError("overlay content must be non-empty")
      if "\x00" in content:
        raise SandboxError("overlay content must not contain NUL")
      try:
        encoded = content.encode("utf-8")
      except UnicodeEncodeError as exc:
        raise SandboxError("overlay content must be valid UTF-8") from exc
      validated_overlays[key] = encoded
  inventory = list(extra_paths or [])
  if validated_overlays:
    inventory.extend(validated_overlays.keys())
  files = source_files(worktree, inventory)
  with tempfile.TemporaryDirectory(prefix="two-site-agent-tests-") as temporary:
    snapshot = Path(temporary) / "source"
    snapshot.mkdir()
    total = 0
    for value in files:
      original = worktree / value
      total += original.stat().st_size
      if total > snapshot_limit:
        raise SandboxError("source snapshot exceeds reviewed capacity")
      destination = snapshot / value
      destination.parent.mkdir(parents=True, exist_ok=True)
      shutil.copyfile(original, destination, follow_symlinks=False)
    snapshot_files = set(files)
    for key, encoded in validated_overlays.items():
      destination = snapshot / key
      destination.parent.mkdir(parents=True, exist_ok=True)
      replaced_bytes = destination.stat().st_size if destination.exists() else 0
      if total - replaced_bytes + len(encoded) > snapshot_limit:
        raise SandboxError("source snapshot exceeds reviewed capacity")
      destination.write_bytes(encoded)
      total = total - replaced_bytes + len(encoded)
      snapshot_files.add(key)
    for selector in selected_tests:
      if not (snapshot / selector.split("::", 1)[0]).is_file():
        raise SandboxError("test selector not in authorized source snapshot")
    if not (snapshot / "config/unit-test.env.example").is_file():
      raise SandboxError("hermetic unit-test environment example missing")
    if not (snapshot / "scripts/two_site_pre_review_pytest_gate.py").is_file():
      raise SandboxError("pre-review pytest gate missing from authorized source snapshot")
    ancestry = None
    if git_ancestry is not None:
      ancestry = materialize_git_ancestry(
        worktree,
        snapshot,
        git_ancestry,
        byte_limit=min(MAX_ANCESTRY_BYTES, snapshot_limit - total),
      )
      total += int(ancestry["commit_object_bytes"])
    channel = _DiagnosticFIFO() if structured_diagnostics else None
    primary = None
    try:
      argv = sandbox_argv(snapshot, selected_tests)
      arguments = dict(timeout=timeout, output_limit=MAX_OUTPUT_BYTES,
              memory_limit=MAX_TEST_MEMORY_BYTES, process_limit=MAX_TEST_PROCESSES,
              env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"})
      if channel is not None:
        argv = _diagnostic_argv(argv, channel)
        arguments["_diagnostics"] = channel
      result = _run_bounded_process(argv, **arguments)
    except BaseException as exc:
      primary = exc
      raise
    finally:
      if channel is not None:
        channel.close()
        if primary is not None and channel.error is not None:
          primary.add_note("diagnostic cleanup or availability failure")
    returned = {"returncode": result.returncode,
        "stdout": result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr.decode("utf-8", errors="replace"),
        "snapshot_files": len(snapshot_files), "snapshot_bytes": total,
        "git_ancestry": ancestry,
        "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"}
    if channel is not None:
      returned.update(pytest_diagnostics=channel.artifact, pytest_diagnostics_error=channel.error)
    return returned
