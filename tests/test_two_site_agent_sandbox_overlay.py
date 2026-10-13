"""Tests for tracked scripts.two_site_agent_sandbox.run_tests overlays boundary."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.two_site_agent_sandbox import (
    SandboxError,
    _run_bounded_process,
    run_tests,
)

EXISTING_PATH = "tests/test_existing.py"
NEW_PATH = "tests/test_new.py"
ENV_PATH = "config/unit-test.env.example"
GATE_PATH = "scripts/two_site_pre_review_pytest_gate.py"

ORIGINAL_EXISTING_CONTENT = "def test_original():\n    assert True\n"
ENV_CONTENT = "APP_ENV=test\n"
GATE_SOURCE = Path(__file__).resolve().parent.parent / GATE_PATH
if not GATE_SOURCE.is_file():
    GATE_SOURCE = Path(GATE_PATH)
with GATE_SOURCE.open("rb") as gate_stream:
    REAL_GATE_BYTES = gate_stream.read(12001)
assert len(REAL_GATE_BYTES) <= 12000
REAL_GATE_SIZE = len(REAL_GATE_BYTES)
REAL_GATE_SHA = hashlib.sha256(REAL_GATE_BYTES).hexdigest()


def create_test_repo(path: Path) -> Path:
    """Build a tiny temporary Git repository with config/unit-test.env.example and one tracked test."""
    repo = path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "init", "-q"],
        check=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    (repo / "config").mkdir(parents=True)
    (repo / ENV_PATH).write_text(ENV_CONTENT, encoding="utf-8")
    (repo / "tests").mkdir(parents=True)
    (repo / EXISTING_PATH).write_text(ORIGINAL_EXISTING_CONTENT, encoding="utf-8")
    gate_dest = repo / GATE_PATH
    gate_dest.parent.mkdir(parents=True, exist_ok=True)
    gate_dest.write_bytes(REAL_GATE_BYTES)
    copied_bytes = gate_dest.read_bytes()
    assert copied_bytes == REAL_GATE_BYTES
    assert len(copied_bytes) == REAL_GATE_SIZE
    assert hashlib.sha256(copied_bytes).hexdigest() == REAL_GATE_SHA
    subprocess.run(
        ["git", "-C", str(repo), "add", "--all"],
        check=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Sandbox Test",
            "-c",
            "user.email=sandbox@test.invalid",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return create_test_repo(tmp_path)


class ProcessRecorder:
    """Record bounded process calls and inspect disposable snapshot before cleanup."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.snapshot_inspections: list[dict[str, object]] = []

    def __call__(self, argv: list[str], *args, **kwargs) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(argv))
        if argv and argv[0] == "/usr/bin/prlimit":
            work_idx = argv.index("/work")
            snapshot_dir = Path(argv[work_idx - 1])
            files: dict[str, bytes] = {}
            for item in snapshot_dir.rglob("*"):
                if item.is_file():
                    files[str(item.relative_to(snapshot_dir))] = item.read_bytes()
            self.snapshot_inspections.append({
                "snapshot_dir": snapshot_dir,
                "files": files,
            })
            return subprocess.CompletedProcess(list(argv), 0, b"2 passed\n", b"")
        return _run_bounded_process(argv, *args, **kwargs)


def test_overlay_replacement_and_new_file_isolated_in_snapshot(repo: Path) -> None:
    """Prove overlay replacement and new-file exist only in disposable snapshot and worktree is untouched."""
    overlay_existing = "def test_replaced():\n    assert 1 == 1\n"
    overlay_new = "def test_new():\n    assert 2 == 2\n"
    overlays = {
        EXISTING_PATH: overlay_existing,
        NEW_PATH: overlay_new,
    }
    selected_tests = [EXISTING_PATH, NEW_PATH]

    recorder = ProcessRecorder()
    with patch("scripts.two_site_agent_sandbox._run_bounded_process", side_effect=recorder):
        result = run_tests(repo, selected_tests, overlays=overlays)

    assert len(recorder.snapshot_inspections) == 1
    inspection = recorder.snapshot_inspections[0]
    snapshot_dir = inspection["snapshot_dir"]
    assert isinstance(snapshot_dir, Path)
    snapshot_files = inspection["files"]
    assert isinstance(snapshot_files, dict)

    assert snapshot_files.get(EXISTING_PATH) == overlay_existing.encode("utf-8")
    assert snapshot_files.get(NEW_PATH) == overlay_new.encode("utf-8")
    assert snapshot_files.get(ENV_PATH) == ENV_CONTENT.encode("utf-8")
    assert snapshot_files.get(GATE_PATH) == REAL_GATE_BYTES

    for selector in selected_tests:
        assert selector in snapshot_files

    assert not snapshot_dir.exists()

    expected_files_count = 4
    expected_bytes_total = (
        len(ENV_CONTENT.encode("utf-8"))
        + len(overlay_existing.encode("utf-8"))
        + len(overlay_new.encode("utf-8"))
        + len(REAL_GATE_BYTES)
    )
    assert result["snapshot_files"] == expected_files_count
    assert result["snapshot_bytes"] == expected_bytes_total
    assert result["returncode"] == 0
    assert result["isolation"] == "bwrap:ro-source,empty-home,clearenv,unshare-all"

    assert (repo / EXISTING_PATH).read_text(encoding="utf-8") == ORIGINAL_EXISTING_CONTENT
    assert not (repo / NEW_PATH).exists()
    assert (repo / ENV_PATH).read_text(encoding="utf-8") == ENV_CONTENT
    assert (repo / GATE_PATH).read_bytes() == REAL_GATE_BYTES
    git_status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    assert git_status.stdout.strip() == ""

    final_argv = recorder.calls[-1]
    assert final_argv[0] == "/usr/bin/prlimit"

    work_idx = final_argv.index("/work")
    assert final_argv[work_idx - 2:work_idx] == ["--ro-bind", str(snapshot_dir)]

    root_idx = final_argv.index("/root")
    assert final_argv[root_idx - 1] == "--dir"

    assert "--clearenv" in final_argv
    assert "--unshare-all" in final_argv
    assert "--unshare-user" in final_argv
    assert "--disable-userns" in final_argv
    assert "--die-with-parent" in final_argv
    assert "--new-session" in final_argv
    assert "--share-net" not in final_argv
    gate_flag_idx = final_argv.index("scripts.two_site_pre_review_pytest_gate")
    assert final_argv[gate_flag_idx - 1:gate_flag_idx + 1] == ["-p", "scripts.two_site_pre_review_pytest_gate"]
    assert final_argv[-2:] == selected_tests


@pytest.mark.parametrize(
    ("overlays", "extra_paths", "snapshot_limit", "expected_match"),
    [
        (["tests/test_existing.py"], None, 300_000_000, "overlays must be a mapping"),
        ("not_a_mapping", None, 300_000_000, "overlays must be a mapping"),
        (12345, None, 300_000_000, "overlays must be a mapping"),
        (
            {f"tests/test_{i}.py": "def test(): pass" for i in range(33)},
            None,
            300_000_000,
            "overlays mapping exceeds 32 entries",
        ),
        ({"../test_escape.py": "pass"}, None, 300_000_000, "escapes the assigned worktree"),
        ({"tests//test_existing.py": "pass"}, None, 300_000_000, "source path escapes the assigned worktree"),
        ({".git/config": "pass"}, None, 300_000_000, "overlay path in excluded root"),
        ({".venv/test.py": "pass"}, None, 300_000_000, "overlay path in excluded root"),
        ({"secrets/token.py": "pass"}, None, 300_000_000, "overlay path exposes credential or private location"),
        ({".env": "TOKEN=1"}, None, 300_000_000, "overlay path exposes environment file"),
        ({"data/items.json": "{}"}, None, 300_000_000, "overlay path in restricted data/archive location"),
        ({"tests/test.exe": "pass"}, None, 300_000_000, "overlay path has unsupported suffix"),
        ({"tests/test_overlap.py": "pass"}, ["tests/test_overlap.py"], 300_000_000, "overlay path overlaps extra_paths"),
        ({EXISTING_PATH: 12345}, None, 300_000_000, "overlay content must be a string"),
        ({EXISTING_PATH: b"bytes"}, None, 300_000_000, "overlay content must be a string"),
        ({EXISTING_PATH: None}, None, 300_000_000, "overlay content must be a string"),
        ({EXISTING_PATH: ""}, None, 300_000_000, "overlay content must be non-empty"),
        ({EXISTING_PATH: "def test():\x00pass"}, None, 300_000_000, "overlay content must not contain NUL"),
        ({EXISTING_PATH: "def test(): pass \ud800"}, None, 300_000_000, "overlay content must be valid UTF-8"),
        ({NEW_PATH: "x" * 200}, None, 100, "source snapshot exceeds reviewed capacity"),
    ],
)
def test_invalid_overlays_fail_before_execution(
    repo: Path,
    overlays: object,
    extra_paths: list[str] | None,
    snapshot_limit: int,
    expected_match: str,
) -> None:
    """Prove malformed, excessive, unsafe, overlapping, invalid, and oversized overlays fail before execution."""
    recorder = ProcessRecorder()
    with patch("scripts.two_site_agent_sandbox._run_bounded_process", side_effect=recorder):
        with pytest.raises(SandboxError, match=expected_match):
            run_tests(
                repo,
                [EXISTING_PATH],
                extra_paths=extra_paths,
                snapshot_limit=snapshot_limit,
                overlays=overlays,
            )

        for call_argv in recorder.calls:
            assert call_argv[0] != "/usr/bin/prlimit", f"Final process executed unexpectedly: {call_argv}"
        assert len(recorder.snapshot_inspections) == 0


@pytest.mark.parametrize("overlays", [None, {}])
def test_omitted_and_empty_overlays_preserve_default_behavior(repo: Path, overlays: dict[str, str] | None) -> None:
    """Prove omitted and empty overlays preserve default behavior and sandbox isolation flags."""
    recorder = ProcessRecorder()
    with patch("scripts.two_site_agent_sandbox._run_bounded_process", side_effect=recorder):
        result = run_tests(repo, [EXISTING_PATH], overlays=overlays)

    assert result["returncode"] == 0
    assert result["isolation"] == "bwrap:ro-source,empty-home,clearenv,unshare-all"
    assert result["snapshot_files"] == 3
    assert recorder.snapshot_inspections[0]["files"].get(GATE_PATH) == REAL_GATE_BYTES
    expected_bytes = (
        len(ENV_CONTENT.encode("utf-8"))
        + len(ORIGINAL_EXISTING_CONTENT.encode("utf-8"))
        + len(REAL_GATE_BYTES)
    )
    assert result["snapshot_bytes"] == expected_bytes

    assert len(recorder.calls) == 2
    final_argv = recorder.calls[-1]
    assert final_argv[0] == "/usr/bin/prlimit"

    work_idx = final_argv.index("/work")
    assert final_argv[work_idx - 2:work_idx] == ["--ro-bind", str(recorder.snapshot_inspections[0]["snapshot_dir"])]

    root_idx = final_argv.index("/root")
    assert final_argv[root_idx - 1] == "--dir"

    assert "--clearenv" in final_argv
    assert "--unshare-all" in final_argv
    assert "--unshare-user" in final_argv
    assert "--disable-userns" in final_argv
    assert "--die-with-parent" in final_argv
    assert "--new-session" in final_argv
    assert "--share-net" not in final_argv
    gate_flag_idx = final_argv.index("scripts.two_site_pre_review_pytest_gate")
    assert final_argv[gate_flag_idx - 1:gate_flag_idx + 1] == ["-p", "scripts.two_site_pre_review_pytest_gate"]
    assert final_argv[-1] == EXISTING_PATH

    assert (repo / EXISTING_PATH).read_text(encoding="utf-8") == ORIGINAL_EXISTING_CONTENT
    assert not (repo / NEW_PATH).exists()
    assert (repo / GATE_PATH).read_bytes() == REAL_GATE_BYTES
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    assert status.stdout.strip() == ""

    with pytest.raises(SandboxError, match="test selector not in authorized source snapshot"):
        run_tests(repo, [NEW_PATH], overlays=overlays)

def install_native_namespace_adapter(monkeypatch):
  """Only outer /work uid65534 adapts namespace args to a real bounded prlimit
  child with unchanged proposed Gate, environment, cwd and private FIFO.
  HOST retains actual bwrap; no reports, process outputs or receipts are stubbed.
  """
  import os
  from scripts import two_site_agent_sandbox as sdk
  if Path.cwd() != Path("/work") or os.geteuid() != 65534:
    return False
  actual = sdk._run_bounded_process

  def child(argv, **kwargs):
    if not argv or argv[0] != "/usr/bin/prlimit":
      return actual(argv, **kwargs)
    boundary = argv.index("--") + 1
    snapshot = Path(argv[argv.index("/work") - 1])
    env = {}
    for index, v in enumerate(argv):
      if v == "--setenv":
        env[argv[index + 1]] = argv[index + 2]
    env["PYTHONPATH"] = str(snapshot)
    env["GIT_CONFIG_VALUE_0"] = str(snapshot)
    if sdk.DIAGNOSTIC_ENV in env:
      binding = argv.index("--bind")
      assert argv[binding + 2] == "/tmp/two-site-pytest-diagnostics.fifo"
      env[sdk.DIAGNOSTIC_ENV] = argv[binding + 1]
    options = dict(kwargs)
    options.update(cwd=snapshot, env=env)
    command = argv[:boundary] + argv[argv.index("/usr/bin/python3"):]
    return actual(command, **options)

  monkeypatch.setattr(sdk, "_run_bounded_process", child)
  return True


def test_native_report_dictionary_callbacks_are_unsupported(repo, monkeypatch):
    from scripts import two_site_agent_sandbox as sdk
    install_native_namespace_adapter(monkeypatch)
    source = """from _pytest.reports import TestReport
class Bomb(dict):
    def get(self,*args): raise RuntimeError('private callback')
    def __getitem__(self,key): raise RuntimeError('private callback')
original=TestReport.from_item_and_call.__func__
def report(cls,item,call):
    real=original(cls,item,call)
    real.__dict__=Bomb(real.__dict__)
    return real
TestReport.from_item_and_call=classmethod(report)
def test_bomb():
    raise ValueError('private payload')
"""
    result = sdk.run_tests(repo, [EXISTING_PATH], overlays={EXISTING_PATH: source},
                           timeout=15, structured_diagnostics=True)
    assert result["returncode"] == 1
    assert result["pytest_diagnostics_error"] is None
    assert result["pytest_diagnostics"] == {"version": 1, "records": [], "partial": True}


def test_native_diagnostic_read_failure_restores_same_witness(repo, monkeypatch):
    from scripts import two_site_agent_sandbox as sdk
    install_native_namespace_adapter(monkeypatch)
    source = "def test_subject():\n    raise ValueError('private')\n"
    def run():
        return sdk.run_tests(repo, [EXISTING_PATH], overlays={EXISTING_PATH: source},
                             timeout=15, structured_diagnostics=True)
    def good():
        result = run()
        assert result["returncode"] == 1 and result["pytest_diagnostics_error"] is None
        assert result["pytest_diagnostics"]["records"][0]["exception"] == "builtins.ValueError"
    good()
    reader = []
    create, read = sdk._DiagnosticFIFO.__init__, sdk.os.read
    def created(channel):
        create(channel)
        reader.append(channel.reader)
    def failed(fd, size):
        if reader and fd == reader[0]:
            raise OSError("injected read failure")
        return read(fd, size)
    with monkeypatch.context() as scoped:
        scoped.setattr(sdk._DiagnosticFIFO, "__init__", created)
        scoped.setattr(sdk.os, "read", failed)
        result = run()
    assert result["returncode"] == 1 and result["pytest_diagnostics"] is None
    assert result["pytest_diagnostics_error"] is not None
    good()


def test_native_inheritable_controller_lock_does_not_reach_child(repo, monkeypatch):
    import os
    from scripts import two_site_agent_sandbox as sdk
    install_native_namespace_adapter(monkeypatch)
    path = repo / "ap-client-runner-lock.sentinel"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    os.set_inheritable(descriptor, True)
    source = """import os
def test_lock():
    assert os.geteuid()==65534
    for name in os.listdir('/proc/self/fd'):
        try: target=os.readlink('/proc/self/fd/'+name)
        except FileNotFoundError: continue
        assert 'lock.sentinel' not in target and 'report.fifo' not in target
"""
    try:
        result = sdk.run_tests(repo, [EXISTING_PATH], overlays={EXISTING_PATH: source},
                               timeout=15, structured_diagnostics=True)
        assert result["returncode"] == 0
        assert result["pytest_diagnostics_error"] is None
        assert result["pytest_diagnostics"] == {"version": 1, "records": [], "partial": False}
        assert os.fstat(descriptor).st_ino == path.stat().st_ino
    finally:
        os.close(descriptor)
        path.unlink()


def prove_canonical_presession_spoof(root, path):
    from scripts import two_site_agent_sandbox as sdk
    conftest = "import sys,types\nfrom pathlib import Path\nclass AutopilotError(Exception): pass\nm=types.ModuleType('scripts.two_site_codex_autopilot')\nm.__file__=str(Path(__file__).parents[1]/'scripts/two_site_codex_autopilot.py')\nm.AutopilotError=AutopilotError\nsys.modules[m.__name__]=m\n"
    fake = "from scripts.two_site_codex_autopilot import AutopilotError\ndef test_fake():\n    raise AutopilotError('autopilot requires branch refactor/two-site-architecture')\n"
    r = sdk.run_tests(root, [path], overlays={path: fake, "tests/conftest.py": conftest,
        GATE_PATH: REAL_GATE_BYTES.decode()}, timeout=30, structured_diagnostics=True)
    assert r["returncode"] == 1 and r["pytest_diagnostics_error"] is None
    v = r["pytest_diagnostics"]
    assert v["partial"] is True and len(v["records"]) == 1 and v["records"][0]["exception"] is None


def test_diagnostic_runtime_cleanup_and_resource_faults(monkeypatch):
    import os
    import sys
    from scripts import two_site_agent_sandbox as sdk
    before = set(os.listdir("/proc/self/fd"))
    stop, Selector = sdk._stop_process_group, sdk.io_selectors.DefaultSelector
    def stop_fault(process):
        stop(process)
        raise OSError("injected cleanup")
    class CloseFault(Selector):
        def close(self):
            super().close()
            raise OSError("injected cleanup")
    for fault in ("stop", "close", "memory", "process"):
        channel = sdk._DiagnosticFIFO()
        options = {"memory_limit": 1} if fault == "memory" else {"process_limit": 1} if fault == "process" else {}
        code = "import time;time.sleep(30)" if fault != "process" else "import os,time;os.fork();time.sleep(30)"
        try:
            with monkeypatch.context() as scoped:
                if fault == "stop": scoped.setattr(sdk, "_stop_process_group", stop_fault)
                if fault == "close": scoped.setattr(sdk.io_selectors, "DefaultSelector", CloseFault)
                with pytest.raises(SandboxError) as raised:
                    sdk._run_bounded_process([sys.executable, "-c", code], timeout=.15,
                        output_limit=1024, _diagnostics=channel, **options)
            assert ("resource budget" if options else "timeout") in str(raised.value)
            if fault == "close": assert channel.error == "diagnostic process cleanup failure"
        finally:
            channel.close()
        assert not channel.directory.exists()
    assert set(os.listdir("/proc/self/fd")) == before
