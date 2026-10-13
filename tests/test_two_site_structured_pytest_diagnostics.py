"""Real pytest/FIFO proof. HOST uses bwrap; outer /work uid65534 alone adapts
the namespace wrapper to a real prlimit child, preserving Gate/env/cwd/FIFO.
No Report, CallInfo, subprocess output or successful artifact is stubbed.
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from _pytest._code.code import ExceptionInfo
from scripts import two_site_agent_sandbox as sdk
from scripts import two_site_pre_review_pytest_gate as gate

_spec = importlib.util.spec_from_file_location(
  "diagnostic_overlay_fixture", Path(__file__).with_name("test_two_site_agent_sandbox_overlay.py"))
_overlay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_overlay)
N, F, O, C = "nodeid", "phase", "outcome", "returncode"
SUBJECT = "tests/test_subject.py"
X, A, P, R = "exception", "assertion", "partial", "records"
D, E = "pytest_diagnostics", "pytest_diagnostics_error"
ORDINARY_KEYS = {C, "stdout", "stderr", "snapshot_files", "snapshot_bytes", "git_ancestry", "isolation"}
ROOT_KEYS = {"version", R, P}
RECORD_KEYS = {N, F, O, X, A, P}
SHAPE = "Module(body=[Assert(test=Compare(left=Name(id='_', ctx=Load()), ops=[Eq()], comparators=[Constant(value=1)]))], type_ignores=[])"
BRANCH = "scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch refactor/two-site-architecture"
CAUSE = "def test_subject():\n    observed=0\n    assert observed == 1\n"

@pytest.fixture(autouse=True)
def native_route(monkeypatch):
  return _overlay.install_native_namespace_adapter(monkeypatch)

@pytest.fixture
def repo(tmp_path):
  return _overlay.create_test_repo(tmp_path)

def native(repo, code, *, path=SUBJECT, enabled=True, timeout=15):
  return sdk.run_tests(repo, [path], overlays={path: code}, timeout=timeout,
            structured_diagnostics=enabled)

def artifact(r, *, rc, count):
  assert r[C] == rc, r["stderr"]
  assert set(r) == ORDINARY_KEYS | {"pytest_diagnostics", "pytest_diagnostics_error"}
  assert r[E] is None
  v = r[D]
  assert type(v) is dict and set(v) == ROOT_KEYS
  assert type(v["version"]) is int and v["version"] == 1
  assert type(v[P]) is bool
  assert len(v[R]) == count
  for record in v[R]:
    assert set(record) == RECORD_KEYS
    assert record[O] == "failed"
    assert type(record[P]) is bool
  return v

def witness(repo):
  assert artifact(native(repo, CAUSE), rc=1, count=1)[R][0][A] == SHAPE

def test_call_and_default(repo, native_route):
  code = "def test_subject():\n    observed = 0\n    assert observed == 1\n"
  r = native(repo, code)
  v = artifact(r, rc=1, count=1)
  assert v == {"version": 1, P: False, R: [
    {N: "tests/test_subject.py::test_subject", F: "call", O: "failed",
    X: "builtins.AssertionError", A: SHAPE, P: False}]}
  ordinary = native(repo, code, enabled=False)
  assert set(ordinary) == ORDINARY_KEYS
  assert ordinary["stdout"].rsplit(" in ", 1)[0] == r["stdout"].rsplit(" in ", 1)[0]
  assert ordinary["stderr"] == r["stderr"]
  for key in ORDINARY_KEYS - {"stdout", "stderr"}:
    assert ordinary[key] == r[key]
  assert (repo / _overlay.GATE_PATH).read_bytes() == _overlay.REAL_GATE_BYTES
  assert not (repo / SUBJECT).exists()

def test_phases(repo):
  code = r"""import pytest
@pytest.fixture
def broken_setup():
    raise ValueError('private setup')
@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError('private teardown')
def test_setup(broken_setup): pass
def test_teardown(broken_teardown): pass
"""
  v = artifact(native(repo, code), rc=1, count=2)
  assert [(r[F], r[X], r[A], r[P]) for r in v[R]] == [
    ("setup", "builtins.ValueError", None, False), ("teardown", "builtins.RuntimeError", None, False)]
  v = artifact(native(repo, "raise ValueError('private collect')\n"), rc=2, count=1)
  assert v[R][0] == {N: SUBJECT, F: "collect", O: "failed",
                 X: "builtins.ValueError", A: None, P: False}

@pytest.mark.parametrize("code,rc,console", [
  ("def test_ok():\n    assert True\n", 0, "1 passed"),
  ("import pytest\n@pytest.mark.skip\ndef test_skip(): pass\n", 1, "1 skipped"),
  ("import pytest\n@pytest.mark.xfail\ndef test_xfail():\n    assert False\n", 1, "1 xfailed"),
  ("import pytest\n@pytest.mark.xfail\ndef test_xpass(): pass\n", 1, "1 xpassed"),
  ("# zero tests\n", 5, "no tests ran"),
])
def test_native_exits(repo, code, rc, console):
  r = native(repo, code)
  assert console in r["stdout"]
  assert artifact(r, rc=rc, count=0) == {"version": 1, R: [], P: False}
  default = native(repo, code, enabled=False)
  assert default[C] == rc and set(default) == ORDINARY_KEYS

def test_native_privacy(repo):
  code = r"""import warnings
import sys,types
from pathlib import Path
class AutopilotError(Exception): pass
AutopilotError.__module__='scripts.two_site_codex_autopilot'
fake=types.ModuleType('scripts.two_site_codex_autopilot')
fake.__file__=str(Path.cwd()/'scripts/two_site_codex_autopilot.py')
fake.AutopilotError=AutopilotError
sys.modules[fake.__name__]=fake
def test_spoof():
    print('FAILURES\nE scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch refactor/two-site-architecture')
    warnings.warn('private-warning-' * 1000)
    raise ValueError('private exception payload')
def test_unknown():
    raise AutopilotError('autopilot requires branch refactor/two-site-architecture')
def test_literal():
    secret = 'x'
    assert secret == 'private-literal'
"""
  r = native(repo, code)
  v = artifact(r, rc=1, count=3)
  assert v[P] is True
  assert [(r[X], r[A], r[P]) for r in v[R]] == [
    ("builtins.ValueError", None, False), (None, None, True), ("builtins.AssertionError", None, True)]
  serialized = json.dumps(v)
  assert "private" not in serialized and "autopilot requires branch" not in serialized
  assert "private-warning-" in r["stdout"] and "FAILURES" in r["stdout"]

def test_real_canonical(repo, native_route):
  root = Path.cwd()
  if native_route:
    shutil.copytree(root, repo, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Sandbox Test", "-c", "user.email=sandbox@test.invalid", "add", "--all"], check=True)
    root = repo
  assert (root / "scripts/two_site_codex_autopilot.py").is_file()
  code = r"""from scripts.two_site_codex_autopilot import AutopilotError
from two_site_codex_autopilot import AutopilotError as Bare
class Text(str):
    def __eq__(self, other): raise RuntimeError('private callback')
    def __str__(self): raise RuntimeError('private callback')
def test_right():
    raise AutopilotError('autopilot requires branch refactor/two-site-architecture')
def test_wrong():
    raise AutopilotError('private wrong message')
def test_subclass_arg():
    raise AutopilotError(Text('autopilot requires branch refactor/two-site-architecture'))
def test_bare():
    raise Bare('autopilot requires branch refactor/two-site-architecture')
"""
  path = "tests/test_diagnostic_canonical_subject.py"
  r = sdk.run_tests(root, [path], overlays={path: code, _overlay.GATE_PATH: _overlay.REAL_GATE_BYTES.decode()},
             timeout=30, structured_diagnostics=True)
  v = artifact(r, rc=1, count=4)
  assert [(r[X], r[P]) for r in v[R]] == [
    (BRANCH, False), ("scripts.two_site_codex_autopilot.AutopilotError", False),
    ("scripts.two_site_codex_autopilot.AutopilotError", True),
    ("two_site_codex_autopilot.AutopilotError: autopilot requires branch refactor/two-site-architecture", False)]
  assert v[P] is True and "private wrong message" not in json.dumps(v)
  assert all(len(r[X].encode()) <= 160 for r in v[R])
  _overlay.prove_canonical_presession_spoof(root, path)

def test_guard_and_bool(repo, monkeypatch):
  def bomb(*args): raise AssertionError("private")
  class Bomb:
    __getattribute__ = __bool__ = __hash__ = __str__ = __repr__ = bomb
  monkeypatch.delenv(gate._FIFO_ENV, raising=False)
  gate.pytest_exception_interact(Bomb(), Bomb(), Bomb())
  for v in (0, 1, "false", None, Bomb()):
    with pytest.raises(sdk.SandboxError, match="boolean"):
      sdk.run_tests(repo, [_overlay.EXISTING_PATH], structured_diagnostics=v)
  class Meta(type):
    __hash__ = __getattribute__ = bomb
  class Unknown(Exception, metaclass=Meta): pass
  try: raise Unknown("private")
  except BaseException: info = ExceptionInfo.from_current(exprinfo="")
  assert gate._exception_identity(info) == (None, True, None)
  assert gate._text(Bomb(), 512) is False

def bytes_artifact(**changes):
  v = {"version": 1, R: [{N: "tests/test_a.py::test_a", F: "call", O: "failed",
      X: "builtins.AssertionError", A: SHAPE, P: False}], P: False}
  for key, replacement in changes.items():
    if key in ROOT_KEYS:
      v[key] = replacement
    else:
      v[R][0][key] = replacement
  return json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode()

@pytest.mark.parametrize("changes", [
  {"version": True}, {"version": 1.0}, {"version": 2}, {R: {}}, {P: 1},
  {N: "../tests/test_a.py"}, {N: "tests/../test_a.py"}, {N: "tests/test_a.py\n"},
  {N: "tests/test_a.py::test_a[" + "x" * 512 + "]"}, {F: "unknown"},
  {O: "passed"}, {X: "private arbitrary exception"}, {X: 1},
  {A: "private arbitrary assertion"}, {A: "Module(body=[], type_ignores=[])"},
  {A: SHAPE.replace("id='_'", "id='secret'")},
  {A: SHAPE.replace("Constant(value=1)", "Constant(value='private')")},
  {A: SHAPE.replace("))], type_ignores", "), msg=Constant(value=1))], type_ignores")}, {"extra": True},
  {X: None}, {P: False, R: [
    {N: "tests/test_a.py", F: "call", O: "failed", X: None, A: None, P: True}]},
])
def test_schema(changes):
  with pytest.raises(sdk.SandboxError):
    sdk._diagnostic_artifact(bytes_artifact(**changes))

def test_json():
  valid = bytes_artifact()
  assert sdk._diagnostic_artifact(valid)[R][0][A] == SHAPE
  invalid = [b"", b"\xff", valid[:-1], valid + b"{}", valid + b"x" * 8192,
       valid.replace(b'"version":1', b'"version":1,"version":1'),
       valid.replace(b'"partial":false}', b'"partial":false,"extra":0}'),
       valid.replace(b'tests/test_a.py::test_a', b'tests/test_a.py::test_\\ud800'),
       valid.replace(b'"version":1', b'"version":NaN'),
       valid.replace(b'"version":1', b'"version":Infinity')]
  for raw in invalid:
    with pytest.raises(sdk.SandboxError):
      sdk._diagnostic_artifact(raw)
  partial = {"version": 1, R: [{N: "tests/test_a.py", F: "call", O: "failed",
       X: None, A: None, P: True}], P: True}
  assert sdk._diagnostic_artifact(json.dumps(partial).encode()) == partial
  for text in ("x" * 513, "é" * 257, "\ud800", "\udfff"):
    assert sdk._bounded_text(text, 512) is False
    assert gate._text(text, 512) is False

def test_cumulative(repo):
  code = "".join(f"def test_many_{i}():\n    raise ValueError('private')\n" for i in range(20))
  v = artifact(native(repo, code), rc=1, count=16)
  assert v[P] is True
  assert len(json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode()) <= 8192
  code = "".join(f"def test_many_{i}_{'x'*380}():\n    observed=0\n    assert observed == 1\n" for i in range(16))
  r = native(repo, code)
  assert r[C] == 1
  v = r[D]
  assert v is not None and v[P] is True and 0 < len(v[R]) < 16
  assert len(json.dumps(v, separators=(",", ":")).encode()) <= 8192
  assert gate._json_size(json.loads(bytes_artifact())) == len(bytes_artifact())
  class Bomb(str):
    def encode(self, *args): raise AssertionError("private callback")
  with pytest.raises(ValueError):
    gate._json_size(Bomb("private"))
  code = "import pytest\n@pytest.mark.parametrize('v',[0],ids=['PRIVATE_PARAMETER_SENTINEL'])\ndef test_id(v):\n    raise ValueError('private')\n"
  v = artifact(native(repo, code), rc=1, count=0)
  assert v[P] is True and "PRIVATE_PARAMETER_SENTINEL" not in json.dumps(v)

def test_fifo_isolation(repo, monkeypatch):
  channels, launches = [], []
  original_init, original_run = sdk._DiagnosticFIFO.__init__, sdk._run_bounded_process
  def created(fifo):
    original_init(fifo)
    channels.append(fifo)
    assert stat.S_IMODE(fifo.directory.stat().st_mode) == 0o700
    assert list(fifo.directory.iterdir()) == [fifo.path]
    info = fifo.path.lstat()
    assert stat.S_ISFIFO(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
    assert info.st_uid == os.geteuid() and info.st_nlink == 1
    for fd in (fifo.reader, fifo.keeper):
      assert not os.get_inheritable(fd)
      assert os.fstat(fd).st_ino == info.st_ino
      assert os.get_blocking(fd) is False
      assert 0 < fcntl.fcntl(fd, fcntl.F_GETPIPE_SZ) <= 8192
  def launched(argv, **kwargs):
    if argv[0] == "/usr/bin/prlimit":
      launches.append(list(argv))
      assert "pass_fds" not in kwargs
      offset = argv.index("--bind")
      assert argv[offset:offset + 3] == ["--bind", str(channels[-1].path), "/tmp/two-site-pytest-diagnostics.fifo"]
      assert str(channels[-1].directory) not in argv
    return original_run(argv, **kwargs)
  monkeypatch.setattr(sdk._DiagnosticFIFO, "__init__", created)
  monkeypatch.setattr(sdk, "_run_bounded_process", launched)
  code = r"""import os
from pathlib import Path
def test_isolation():
    channel=Path(os.environ['TWO_SITE_PYTEST_DIAGNOSTICS_FIFO'])
    assert channel.is_fifo()
    assert os.geteuid()==65534 and channel.stat().st_uid==65534
    assert not Path('/root/.codex').exists()
    assert not Path('/root/.ssh').exists()
    assert os.environ['APP_ENV_FILE']=='config/unit-test.env.example'
    assert not any('report.fifo' in os.readlink('/proc/self/fd/'+fd) for fd in os.listdir('/proc/self/fd') if os.path.exists('/proc/self/fd/'+fd))
"""
  r = native(repo, code)
  assert artifact(r, rc=0, count=0)[P] is False
  assert len(launches) == 1
  if Path.cwd() != Path("/work") or os.geteuid() != 65534:
    assert "/usr/bin/bwrap" in launches[0] and "--uid" in launches[0]
    assert launches[0][launches[0].index("--uid") + 1] == "65534"
  assert all(not fifo.directory.exists() and fifo.reader is None and fifo.keeper is None for fifo in channels)

@pytest.mark.parametrize("fault", ["path", "mode", "parent", "reader", "keeper", "hardlink", "unlink", "reader_block", "keeper_block", "extra", "capacity"])
def test_custody_restore(repo, monkeypatch, fault):
  witness(repo)
  orig = sdk._diagnostic_argv
  touched = []
  def corrupted(argv, fifo):
    touched.append(fifo)
    if fault == "path":
      fifo.path.unlink(); os.mkfifo(fifo.path, 0o600)
    elif fault == "mode":
      fifo.path.chmod(0o640)
    elif fault == "parent":
      fifo.directory.chmod(0o750)
    elif fault in ("reader", "keeper"):
      os.close(getattr(fifo, fault))
      setattr(fifo, fault, os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC))
    elif fault.endswith("_block"):
      os.set_blocking(getattr(fifo, fault.split("_")[0]), True)
    elif fault == "hardlink":
      os.link(fifo.path, fifo.directory / "other")
    elif fault == "extra":
      (fifo.directory / "other").touch()
    elif fault == "capacity":
      get = sdk.fcntl.fcntl
      ctx.setattr(sdk.fcntl, "fcntl", lambda fd, op, *a: 8193 if op == fcntl.F_GETPIPE_SZ else get(fd, op, *a))
    else:
      fifo.path.unlink()
    return orig(argv, fifo)
  with monkeypatch.context() as ctx:
    ctx.setattr(sdk, "_diagnostic_argv", corrupted)
    with pytest.raises(sdk.SandboxError, match="custody"):
      native(repo, CAUSE)
  assert touched and touched[0].reader is None and touched[0].keeper is None
  for fifo in touched:
    if fifo.directory.exists():
      for entry in fifo.directory.iterdir(): entry.unlink()
      fifo.directory.rmdir()
  witness(repo)

@pytest.mark.parametrize("fault", ["producer", "reader"])
def test_linkage_restore(repo, monkeypatch, fault):
  witness(repo)
  if fault == "producer":
    faulty = _overlay.REAL_GATE_BYTES.decode().replace("    _records.append(record)", "    return  # injected real producer fault")
    assert faulty != _overlay.REAL_GATE_BYTES.decode()
    r = sdk.run_tests(repo, [SUBJECT], overlays={SUBJECT: CAUSE, _overlay.GATE_PATH: faulty},
               timeout=15, structured_diagnostics=True)
  else:
    def discard(fifo):
      fifo.check()
      try: os.read(fifo.reader, 4096)
      except BlockingIOError: pass
      fifo.check()
    with monkeypatch.context() as ctx:
      ctx.setattr(sdk._DiagnosticFIFO, "drain", discard)
      r = native(repo, CAUSE)
  assert r[C] == 1
  assert r[D] is None or r[D][R] == []
  witness(repo)

@pytest.mark.parametrize("partial", [False, True])
def test_corrupt_stream(repo, partial):
  code = r"""import os
def test_corrupt():
    fd=os.open(os.environ['TWO_SITE_PYTEST_DIAGNOSTICS_FIFO'],os.O_WRONLY|os.O_NONBLOCK)
    os.write(fd,b'{\"version\":true,\"private\":1}')
    os.close(fd)
    raise ValueError('private actual error')
"""
  if partial:
    code = "def test_corrupt():\n    raise ValueError('private actual error')\n"
    faulty = _overlay.REAL_GATE_BYTES.decode().replace("os.write(descriptor, data)", "os.write(descriptor, data[:len(data)//2])")
    assert faulty != _overlay.REAL_GATE_BYTES.decode()
    r = sdk.run_tests(repo, [SUBJECT], overlays={SUBJECT: code, _overlay.GATE_PATH: faulty}, timeout=15, structured_diagnostics=True)
  else:
    r = native(repo, code)
  assert r[C] == 1
  assert r[D] is None and r[E] is not None
  assert "ValueError: private actual error" in r["stdout"]

def test_bounded_source(repo, monkeypatch):
  oversized = "#" + "x" * 40001 + "\ndef test_subject():\n    observed=0\n    assert observed == 1\n"
  v = artifact(native(repo, oversized), rc=1, count=1)
  assert v[P] is True and v[R][0][A] is None
  path = repo / "tests/test_guard.py"
  code = "def fail():\n    observed=0\n    assert observed == 1\n"
  path.write_text(code)
  spec = importlib.util.spec_from_file_location("source_guard", path)
  module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
  monkeypatch.setattr(gate, "_ROOT", repo)
  def shape():
    try: module.fail()
    except AssertionError: return gate._source_assertion(sys.exc_info()[2])
  assert shape() == SHAPE
  linked = path.with_name("linked.py")
  os.link(path, linked)
  try: assert shape() is None
  finally: linked.unlink()
  path.rename(linked); path.symlink_to(linked)
  try: assert shape() is None
  finally: path.unlink(); linked.rename(path)
  read = gate.os.read
  def growth(fd, size):
    data = read(fd, size)
    if data: path.write_text(code + "# grew\n")
    return data
  with monkeypatch.context() as ctx:
    ctx.setattr(gate.os, "read", growth)
    assert shape() is None
  path.write_text(code)
  assert shape() == SHAPE

def test_failure_cleanup(repo, monkeypatch):
  directories = []
  original_mkdir = sdk.tempfile.mkdtemp
  def saved(*args, **kwargs):
    d = original_mkdir(*args, **kwargs)
    if kwargs.get("prefix") == "two-site-pytest-fifo-": directories.append(Path(d))
    return d
  monkeypatch.setattr(sdk.tempfile, "mkdtemp", saved)
  real_fcntl = sdk.fcntl.fcntl
  def capacity_failure(fd, operation, *args):
    if operation == fcntl.F_SETPIPE_SZ: raise OSError("injected fault")
    return real_fcntl(fd, operation, *args)
  before = set(os.listdir("/proc/self/fd"))
  with monkeypatch.context() as ctx:
    ctx.setattr(sdk.fcntl, "fcntl", capacity_failure)
    with pytest.raises(OSError): native(repo, "def test_ok(): pass\n")
  assert directories and all(not path.exists() for path in directories)
  assert set(os.listdir("/proc/self/fd")) == before
  orig = sdk._run_bounded_process
  def failed_launch(argv, **kwargs):
    if argv[0] != "/usr/bin/prlimit": return orig(argv, **kwargs)
    with patch.object(sdk.subprocess, "Popen", side_effect=OSError("injected fault")):
      return orig(argv, **kwargs)
  with monkeypatch.context() as ctx:
    ctx.setattr(sdk, "_run_bounded_process", failed_launch)
    with pytest.raises(OSError): native(repo, "def test_ok(): pass\n")
  assert all(not path.exists() for path in directories)
  assert set(os.listdir("/proc/self/fd")) == before
  fifo = sdk._DiagnosticFIFO()
  real_selector = sdk.io_selectors.DefaultSelector
  children = []
  real_popen = sdk.subprocess.Popen
  def observed_popen(*args, **kwargs):
    process = real_popen(*args, **kwargs); children.append(process); return process
  class BrokenSelector:
    def __init__(self): self.inner = real_selector()
    def register(self, *args): raise OSError("injected fault")
    def close(self): self.inner.close()
  try:
    with patch.object(sdk.io_selectors, "DefaultSelector", BrokenSelector), patch.object(sdk.subprocess, "Popen", observed_popen):
      with pytest.raises(OSError):
        sdk._run_bounded_process([sys.executable, "-c", "import time;time.sleep(30)"], timeout=1, output_limit=1024, _diagnostics=fifo)
    assert children and children[0].poll() is not None
  finally:
    fifo.close()
  assert not fifo.directory.exists() and set(os.listdir("/proc/self/fd")) == before

def test_near_raw_cap(repo):
  code = r"""import os
def test_near(capsys):
    with capsys.disabled():
        os.write(1,b'x'*(8000000-4096))
    observed=0
    assert observed == 1
"""
  r = native(repo, code, timeout=30)
  assert 8000000 - 4096 <= len(r["stdout"].encode()) <= 8000000
  assert artifact(r, rc=1, count=1)[R][0][A] == SHAPE
  with pytest.raises(sdk.SandboxError, match="output exceeded"):
    native(repo, code.replace("8000000-4096", "8000000"), timeout=30)

def test_deadline_flood():
  before = set(os.listdir("/proc/self/fd"))
  fifo = sdk._DiagnosticFIFO()
  try:
    code = ("import os,time;fd=os.open(" + repr(str(fifo.path)) + ",os.O_WRONLY|os.O_NONBLOCK);"
         "os.close(1);os.close(2);time.sleep(30)")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
      sdk._run_bounded_process([sys.executable, "-c", code], timeout=.15, output_limit=1024, _diagnostics=fifo)
    assert time.monotonic() - started < 3
  finally: fifo.close()
  assert not fifo.directory.exists()
  fifo = sdk._DiagnosticFIFO()
  code = ("import os,time;fd=os.open(" + repr(str(fifo.path)) + ",os.O_WRONLY|os.O_NONBLOCK)\n"
       "while True:\n"
       " try: os.write(fd,b'x'*4096)\n except BlockingIOError: pass\n")
  try:
    started = time.monotonic()
    with pytest.raises(sdk.SandboxError, match="timeout"):
      sdk._run_bounded_process([sys.executable, "-c", code], timeout=.15, output_limit=1024, _diagnostics=fifo)
    assert time.monotonic() - started < 3 and len(fifo.data) <= 8193
    assert fifo.error == "diagnostic overflow"
  finally: fifo.close()
  fifo = sdk._DiagnosticFIFO()
  try:
    r = sdk._run_bounded_process([sys.executable, "-c", "import os;os.write(1,b'raw');os.write(2,b'err')"],
                    timeout=1, output_limit=6, _diagnostics=fifo)
    assert r.returncode == 0 and r.stdout == b"raw" and r.stderr == b"err"
    assert fifo.artifact is None and fifo.error is not None
  finally: fifo.close()
  assert set(os.listdir("/proc/self/fd")) == before

def test_cleanup_precedence(repo, monkeypatch):
  original_unlink = sdk.os.unlink
  saved = []
  def broken_unlink(path, *args, **kwargs):
    if path == "report.fifo" and "dir_fd" in kwargs:
      saved.append(Path(os.readlink("/proc/self/fd/" + str(kwargs["dir_fd"]))))
      raise OSError("private cleanup fault")
    return original_unlink(path, *args, **kwargs)
  try:
    with monkeypatch.context() as ctx:
      ctx.setattr(sdk.os, "unlink", broken_unlink)
      r = native(repo, "def test_ok(): pass\n")
      assert r[C] == 0 and r[D] is None
      assert r[E] == "diagnostic cleanup failure"
      with pytest.raises(sdk.SandboxError, match="timeout") as raised:
        native(repo, "import time\ndef test_timeout():\n    time.sleep(30)\n", timeout=1)
      assert "diagnostic cleanup or availability failure" in raised.value.__notes__
  finally:
    for d in set(saved):
      if d.exists():
        for entry in d.iterdir(): entry.unlink()
        d.rmdir()
