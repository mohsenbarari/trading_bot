"""Tests for two_site_cli_capture."""

from __future__ import annotations

import contextlib
import copy
import os
import pathlib
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Optional
from unittest.mock import patch
import pytest

_TEST_FILE = pathlib.Path(__file__).resolve()
_SRC_FILE = _TEST_FILE.parent.parent / "scripts" / "two_site_cli_capture.py"

_SRC_STAT_SIZE = _SRC_FILE.stat().st_size
_TEST_STAT_SIZE = _TEST_FILE.stat().st_size
_TOTAL_STAT_SIZE = _SRC_STAT_SIZE + _TEST_STAT_SIZE

if _SRC_STAT_SIZE > 8000 or _TEST_STAT_SIZE > 24000 or _TOTAL_STAT_SIZE > 32000:
    raise AssertionError(
        f"Byte cap exceeded: src={_SRC_STAT_SIZE} (max 8000), "
        f"test={_TEST_STAT_SIZE} (max 24000), total={_TOTAL_STAT_SIZE} (max 32000)"
    )

with open(_SRC_FILE, "rb") as _f_src:
    _SRC_BYTES = _f_src.read(8001)
if len(_SRC_BYTES) > 8000 or len(_SRC_BYTES) != _SRC_STAT_SIZE:
    raise AssertionError("Invalid src bytes")

with open(_TEST_FILE, "rb") as _f_test:
    _TEST_BYTES = _f_test.read(24001)
if len(_TEST_BYTES) > 24000 or len(_TEST_BYTES) != _TEST_STAT_SIZE:
    raise AssertionError("Invalid test bytes")

import selectors
import scripts.two_site_cli_capture as target_mod
from scripts.two_site_cli_capture import (
    MAX_OUTPUT,
    capture_bounded_process,
)
from scripts.two_site_proposal_runner import (
    STREAM_CHUNK_SIZE,
    ProposalError,
    terminate_process,
)

_ORIG_DEFAULT_SELECTOR = selectors.DefaultSelector


def _make_producer(code: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-u", "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _reap_process(proc: Optional[Any]) -> None:
    if proc is None:
        return
    real = getattr(proc, "_real", proc)
    with contextlib.suppress(Exception):
        terminate_process(real)
    for s in (getattr(real, "stdin", None), getattr(real, "stdout", None), getattr(real, "stderr", None)):
        if s is not None and not s.closed:
            with contextlib.suppress(Exception):
                s.close()


def _snapshot_tree(
    base: pathlib.Path, exclude: Optional[set[pathlib.Path]] = None
) -> dict[str, tuple[str, int, int, int, Optional[bytes]]]:
    snap = {}
    if not base.exists():
        return snap
    ex_set = set(exclude) if exclude else set()
    ex_resolved = {p.resolve() for p in ex_set}
    for p in sorted(base.rglob("*")):
        if p in ex_set or p.resolve() in ex_resolved:
            continue
        st = os.stat(p, follow_symlinks=False)
        m = st.st_mode
        snap[str(p.relative_to(base))] = (
            "dir" if stat.S_ISDIR(m) else "reg" if stat.S_ISREG(m) else "other",
            st.st_uid,
            stat.S_IMODE(m),
            st.st_nlink,
            p.read_bytes() if stat.S_ISREG(m) else None,
        )
    return copy.deepcopy(snap)


def _run_standard_good(out_p: pathlib.Path, err_p: pathlib.Path) -> None:
    proc = _make_producer(
        "import sys\n"
        "d = sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(d + b'\\n')\n"
        "sys.stderr.buffer.write(b'ok\\n')\n"
    )
    try:
        res = capture_bounded_process(proc, b"GOOD_PAYLOAD", out_p, err_p, timeout=5.0, max_output=MAX_OUTPUT)
        assert res is None
        assert proc.poll() == 0
        assert proc.stdin.closed and proc.stdout.closed and proc.stderr.closed
        assert out_p.read_bytes() == b"GOOD_PAYLOAD\n"
        assert err_p.read_bytes() == b"ok\n"
    finally:
        _reap_process(proc)


class SelectorSpy:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._real = _ORIG_DEFAULT_SELECTOR(*args, **kwargs)
        self.close_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def close(self) -> None:
        self.close_count += 1
        self._real.close()


class MissingPipeWrapper:
    def __init__(self, real_proc: subprocess.Popen, missing_attr: str) -> None:
        self._real = real_proc
        self.missing_attr = missing_attr

    def __getattr__(self, name: str) -> Any:
        if name == self.missing_attr:
            return None
        return getattr(self._real, name)


def _run_fault_scenario(
    fixture_dir: pathlib.Path,
    case_name: str,
    *,
    bad_data: dict[str, Any],
    make_bad_proc: Callable[[], Any],
    expected_exc_type: type[BaseException],
    expected_msg: Optional[str] = None,
    custom_assert_error: Optional[Callable[[BaseException], None]] = None,
    custom_assert_evidence: Optional[Callable[[], None]] = None,
    custom_restore: Optional[Callable[[], None]] = None,
    allow_untouched_proc: bool = False,
    patch_ctxs: Optional[list[Any]] = None,
) -> None:
    scenario_dir = fixture_dir / case_name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    canary = scenario_dir / "static_canary.txt"
    if not canary.exists():
        canary.write_bytes(b"STATIC_CANARY_PAYLOAD_V1")
        os.chmod(canary, 0o644)

    good_before_out = scenario_dir / "good_before_stdout.log"
    good_before_err = scenario_dir / "good_before_stderr.log"
    good_after_out = scenario_dir / "good_after_stdout.log"
    good_after_err = scenario_dir / "good_after_stderr.log"

    _run_standard_good(good_before_out, good_before_err)

    bad_data_snap = copy.deepcopy(bad_data)
    exclude_paths = {
        good_before_out,
        good_before_err,
        good_after_out,
        good_after_err,
        bad_data["stdout_path"],
        bad_data["stderr_path"],
    }
    static_snap = _snapshot_tree(scenario_dir, exclude_paths)

    bad_proc = make_bad_proc()
    try:
        def invoke_call() -> None:
            capture_bounded_process(
                bad_proc,
                bad_data["input_bytes"],
                bad_data["stdout_path"],
                bad_data["stderr_path"],
                bad_data["timeout"],
                bad_data["max_output"],
            )

        with pytest.raises(expected_exc_type) as exc_info:
            if patch_ctxs:
                with contextlib.ExitStack() as stack:
                    for p in patch_ctxs:
                        stack.enter_context(p)
                    invoke_call()
            else:
                invoke_call()

        err = exc_info.value
        if expected_msg is not None:
            assert expected_msg in str(err)
        if custom_assert_error is not None:
            custom_assert_error(err)

        if not allow_untouched_proc:
            assert bad_proc.poll() is not None
            for s in (bad_proc.stdin, bad_proc.stdout, bad_proc.stderr):
                if s is not None:
                    assert s.closed

        if custom_assert_evidence is not None:
            custom_assert_evidence()

        assert bad_data == bad_data_snap
        assert _snapshot_tree(scenario_dir, exclude_paths) == static_snap
    finally:
        _reap_process(bad_proc)
        if custom_restore is not None:
            custom_restore()

    _run_standard_good(good_after_out, good_after_err)
    assert bad_data == bad_data_snap
    assert _snapshot_tree(scenario_dir, exclude_paths) == static_snap


def test_case1_good_scenarios(tmp_path: pathlib.Path) -> None:
    cases = [
        (
            "200k",
            "import sys\nd=sys.stdin.buffer.read()\nsys.stdout.buffer.write(d+b'\\n')\nsys.stderr.buffer.write(b'ok\\n')",
            b"B" * 200000,
            b"B" * 200000 + b"\n",
            b"ok\n",
            0,
        ),
        (
            "backpressure",
            "import sys,time\ntime.sleep(0.05)\nsys.stdin.buffer.read()",
            b"P" * 150000,
            b"",
            b"",
            0,
        ),
        (
            "zero_input",
            "import sys\nassert len(sys.stdin.buffer.read())==0\nsys.stdout.buffer.write(b'zero-in\\n')\nsys.stderr.buffer.write(b'err-ok\\n')",
            b"",
            b"zero-in\n",
            b"err-ok\n",
            0,
        ),
        (
            "binary_version",
            "import sys\nsys.stdout.buffer.write(b'tool v1.2.3\\x00\\xff\\n')\nsys.stderr.buffer.write(b'info: init\\xfe\\n')",
            b"",
            b"tool v1.2.3\x00\xff\n",
            b"info: init\xfe\n",
            0,
        ),
        (
            "nonzero_exit",
            "import sys\nsys.stdout.buffer.write(b'step fail\\n')\nsys.stderr.buffer.write(b'fatal\\n')\nsys.exit(42)",
            b"",
            b"step fail\n",
            b"fatal\n",
            42,
        ),
    ]
    for name, code, in_b, exp_out, exp_err, exp_code in cases:
        scenario_dir = tmp_path / f"c1_{name}"
        scenario_dir.mkdir(parents=True, exist_ok=True)
        out_p = scenario_dir / f"c1_{name}_out.log"
        err_p = scenario_dir / f"c1_{name}_err.log"
        proc = _make_producer(code)
        try:
            res = capture_bounded_process(proc, in_b, out_p, err_p, timeout=10.0, max_output=MAX_OUTPUT)
            assert res is None
            assert proc.poll() == exp_code
            assert proc.stdin.closed and proc.stdout.closed and proc.stderr.closed
            assert out_p.read_bytes() == exp_out
            assert err_p.read_bytes() == exp_err
        finally:
            _reap_process(proc)


def test_case2_shared_output_exact_limit_succeeds(tmp_path: pathlib.Path) -> None:
    scenario_dir = tmp_path / "c2_exact"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    out_p = scenario_dir / "c2_exact_stdout.log"
    err_p = scenario_dir / "c2_exact_stderr.log"
    code = (
        "import sys\n"
        "sys.stdout.buffer.write(b'O' * 60)\n"
        "sys.stdout.buffer.flush()\n"
        "sys.stderr.buffer.write(b'E' * 40)\n"
        "sys.stderr.buffer.flush()\n"
    )
    proc = _make_producer(code)
    try:
        res = capture_bounded_process(proc, b"", out_p, err_p, timeout=5.0, max_output=100)
        assert res is None
        assert proc.poll() == 0
        assert proc.stdin.closed and proc.stdout.closed and proc.stderr.closed
        assert out_p.read_bytes() == b"O" * 60
        assert err_p.read_bytes() == b"E" * 40
        assert len(out_p.read_bytes()) + len(err_p.read_bytes()) == 100
    finally:
        _reap_process(proc)


def test_case2_shared_output_overflow_rejections(tmp_path: pathlib.Path) -> None:
    scenario1_dir = tmp_path / "c2_limit_plus_one"
    out1 = scenario1_dir / "c2_ov1_stdout.log"
    err1 = scenario1_dir / "c2_ov1_stderr.log"

    def make_proc1() -> subprocess.Popen:
        code = (
            "import sys\n"
            "sys.stdout.buffer.write(b'O' * 60)\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stderr.buffer.write(b'E' * 41)\n"
            "sys.stderr.buffer.flush()\n"
        )
        return _make_producer(code)

    def assert_evidence1() -> None:
        assert out1.exists() and err1.exists()
        assert len(out1.read_bytes()) + len(err1.read_bytes()) == 100
        assert out1.read_bytes().startswith(b"O" * min(60, len(out1.read_bytes())))
        assert err1.read_bytes().startswith(b"E" * min(41, len(err1.read_bytes())))

    _run_fault_scenario(
        tmp_path,
        "c2_limit_plus_one",
        bad_data={"input_bytes": b"", "stdout_path": out1, "stderr_path": err1, "timeout": 5.0, "max_output": 100},
        make_bad_proc=make_proc1,
        expected_exc_type=ProposalError,
        expected_msg="CLI output exceeds budget",
        custom_assert_evidence=assert_evidence1,
    )

    scenario2_dir = tmp_path / "c2_large_pressure"
    out2 = scenario2_dir / "c2_press_stdout.log"
    err2 = scenario2_dir / "c2_press_stderr.log"

    def make_proc2() -> subprocess.Popen:
        code = (
            "import sys\n"
            "sys.stdout.buffer.write(b'A' * 10000)\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stderr.buffer.write(b'B' * 10000)\n"
            "sys.stderr.buffer.flush()\n"
        )
        return _make_producer(code)

    def assert_evidence2() -> None:
        assert out2.exists() and err2.exists()
        assert len(out2.read_bytes()) + len(err2.read_bytes()) == 500

    _run_fault_scenario(
        tmp_path,
        "c2_large_pressure",
        bad_data={"input_bytes": b"", "stdout_path": out2, "stderr_path": err2, "timeout": 5.0, "max_output": 500},
        make_bad_proc=make_proc2,
        expected_exc_type=ProposalError,
        expected_msg="CLI output exceeds budget",
        custom_assert_evidence=assert_evidence2,
    )


def test_case2_early_kill_before_late_marker(tmp_path: pathlib.Path) -> None:
    scenario_dir = tmp_path / "c2_early_kill"
    out_p = scenario_dir / "c2_early_stdout.log"
    err_p = scenario_dir / "c2_early_stderr.log"

    def make_proc() -> subprocess.Popen:
        code = (
            "import sys, time\n"
            "sys.stdout.buffer.write(b'X' * 150)\n"
            "sys.stdout.buffer.flush()\n"
            "time.sleep(5)\n"
            "sys.stdout.buffer.write(b'LATE_MARKER')\n"
            "sys.stdout.buffer.flush()\n"
        )
        return _make_producer(code)

    t0 = 0.0

    def assert_error(err: BaseException) -> None:
        assert isinstance(err, ProposalError)
        assert str(err) == "CLI output exceeds budget"
        assert time.monotonic() - t0 < 2.5

    def assert_evidence() -> None:
        assert out_p.exists()
        out_content = out_p.read_bytes()
        assert len(out_content) == 100
        assert b"LATE_MARKER" not in out_content

    t0 = time.monotonic()
    _run_fault_scenario(
        tmp_path,
        "c2_early_kill",
        bad_data={"input_bytes": b"", "stdout_path": out_p, "stderr_path": err_p, "timeout": 10.0, "max_output": 100},
        make_bad_proc=make_proc,
        expected_exc_type=ProposalError,
        expected_msg="CLI output exceeds budget",
        custom_assert_error=assert_error,
        custom_assert_evidence=assert_evidence,
    )


def test_case2_os_read_remaining_plus_one_proof(tmp_path: pathlib.Path) -> None:
    scenario_dir = tmp_path / "c2_spy"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    out_p = scenario_dir / "c2_spy_out.log"
    err_p = scenario_dir / "c2_spy_err.log"
    code = (
        "import sys\n"
        "sys.stdout.buffer.write(b'Z' * 50)\n"
        "sys.stdout.buffer.flush()\n"
    )
    proc = _make_producer(code)
    requested_lengths = []
    orig_read = os.read

    def spy_read(fd: int, length: int) -> bytes:
        if proc.stdout is not None and fd == proc.stdout.fileno():
            requested_lengths.append(length)
        return orig_read(fd, length)

    try:
        with patch("os.read", side_effect=spy_read):
            res = capture_bounded_process(proc, b"", out_p, err_p, timeout=5.0, max_output=150)
        assert res is None
        assert proc.poll() == 0
        assert out_p.read_bytes() == b"Z" * 50
        assert 151 in requested_lengths
        assert MAX_OUTPUT == 2_000_000
    finally:
        _reap_process(proc)


def test_case3_timeout_expired_reap_and_selector_close(tmp_path: pathlib.Path) -> None:
    scenario_dir = tmp_path / "c3_timeout"
    out_p = scenario_dir / "c3_to_stdout.log"
    err_p = scenario_dir / "c3_to_stderr.log"
    spy_holder: list[SelectorSpy] = []

    def make_spy_selector(*a: Any, **k: Any) -> SelectorSpy:
        s = SelectorSpy(*a, **k)
        spy_holder.append(s)
        return s

    def assert_evidence() -> None:
        assert out_p.exists() and out_p.read_bytes() == b""
        assert err_p.exists() and err_p.read_bytes() == b""
        assert len(spy_holder) == 1
        assert spy_holder[0].close_count == 1
        assert spy_holder[0].get_map() is None

    _run_fault_scenario(
        tmp_path,
        "c3_timeout",
        bad_data={"input_bytes": b"", "stdout_path": out_p, "stderr_path": err_p, "timeout": 0.2, "max_output": MAX_OUTPUT},
        make_bad_proc=lambda: _make_producer("import sys, time\ntime.sleep(10)\n"),
        expected_exc_type=subprocess.TimeoutExpired,
        custom_assert_evidence=assert_evidence,
        patch_ctxs=[patch("selectors.DefaultSelector", side_effect=make_spy_selector)],
    )


def test_case3_invalid_configuration_max_output_and_pipes(tmp_path: pathlib.Path) -> None:
    for bad_max in (0, -1):
        case_label = f"c3_invalid_max_{abs(bad_max)}"
        scenario_dir = tmp_path / case_label
        out_p = scenario_dir / f"{case_label}_out.log"
        err_p = scenario_dir / f"{case_label}_err.log"
        procs: list[subprocess.Popen] = []

        def make_proc() -> subprocess.Popen:
            p = _make_producer("import sys, time\ntime.sleep(5)\n")
            procs.append(p)
            return p

        def assert_evidence() -> None:
            assert not out_p.exists() and not err_p.exists()
            assert procs[-1].poll() is None

        _run_fault_scenario(
            tmp_path,
            case_label,
            bad_data={"input_bytes": b"", "stdout_path": out_p, "stderr_path": err_p, "timeout": 5.0, "max_output": bad_max},
            make_bad_proc=make_proc,
            expected_exc_type=ProposalError,
            expected_msg="invalid bounded process configuration",
            custom_assert_evidence=assert_evidence,
            allow_untouched_proc=True,
        )

    for missing_name in ("stdin", "stdout", "stderr"):
        case_label = f"c3_missing_{missing_name}"
        scenario_dir = tmp_path / case_label
        out_p = scenario_dir / f"{case_label}_out.log"
        err_p = scenario_dir / f"{case_label}_err.log"
        procs_m: list[subprocess.Popen] = []

        def make_missing_proc(mname: str = missing_name) -> MissingPipeWrapper:
            p = _make_producer("import sys, time\ntime.sleep(5)\n")
            procs_m.append(p)
            return MissingPipeWrapper(p, mname)

        def assert_missing_evidence() -> None:
            assert not out_p.exists() and not err_p.exists()
            assert procs_m[-1].poll() is None

        _run_fault_scenario(
            tmp_path,
            case_label,
            bad_data={"input_bytes": b"", "stdout_path": out_p, "stderr_path": err_p, "timeout": 5.0, "max_output": MAX_OUTPUT},
            make_bad_proc=make_missing_proc,
            expected_exc_type=ProposalError,
            expected_msg="invalid bounded process configuration",
            custom_assert_evidence=assert_missing_evidence,
            allow_untouched_proc=True,
        )


def test_case4_stdout_and_stderr_xb_collision(tmp_path: pathlib.Path) -> None:
    scenario1_dir = tmp_path / "c4_stdout_collision"
    scenario1_dir.mkdir(parents=True, exist_ok=True)
    out1 = scenario1_dir / "c4_coll_stdout.log"
    err1 = scenario1_dir / "c4_coll_stderr.log"

    def make_proc1() -> subprocess.Popen:
        return _make_producer("import sys, time\ntime.sleep(5)\n")

    out1.write_bytes(b"LITERAL_STDOUT_BYTES")

    def assert_stdout_evidence() -> None:
        assert out1.read_bytes() == b"LITERAL_STDOUT_BYTES"
        assert not err1.exists()

    _run_fault_scenario(
        tmp_path,
        "c4_stdout_collision",
        bad_data={"input_bytes": b"", "stdout_path": out1, "stderr_path": err1, "timeout": 5.0, "max_output": MAX_OUTPUT},
        make_bad_proc=make_proc1,
        expected_exc_type=FileExistsError,
        custom_assert_evidence=assert_stdout_evidence,
        custom_restore=lambda: out1.unlink() if out1.exists() else None,
    )

    scenario2_dir = tmp_path / "c4_stderr_collision"
    scenario2_dir.mkdir(parents=True, exist_ok=True)
    out2 = scenario2_dir / "c4_coll_stdout.log"
    err2 = scenario2_dir / "c4_coll_stderr.log"

    def make_proc2() -> subprocess.Popen:
        return _make_producer("import sys, time\ntime.sleep(5)\n")

    err2.write_bytes(b"LITERAL_STDERR_BYTES")

    def assert_stderr_evidence() -> None:
        assert out2.exists() and out2.read_bytes() == b""
        assert err2.read_bytes() == b"LITERAL_STDERR_BYTES"

    _run_fault_scenario(
        tmp_path,
        "c4_stderr_collision",
        bad_data={"input_bytes": b"", "stdout_path": out2, "stderr_path": err2, "timeout": 5.0, "max_output": MAX_OUTPUT},
        make_bad_proc=make_proc2,
        expected_exc_type=FileExistsError,
        custom_assert_evidence=assert_stderr_evidence,
        custom_restore=lambda: err2.unlink() if err2.exists() else None,
    )


def test_case5_lower_os_read_and_base_exception_identities(tmp_path: pathlib.Path) -> None:
    sentinel_os_err = OSError("sentinel lower os.read failure")
    sentinel_ki = KeyboardInterrupt("sentinel keyboard interrupt")
    sentinel_se = SystemExit(77)

    cases = [
        ("os_error", sentinel_os_err, OSError),
        ("keyboard_interrupt", sentinel_ki, KeyboardInterrupt),
        ("system_exit", sentinel_se, SystemExit),
    ]

    for label, sentinel, exc_type in cases:
        case_label = f"c5_{label}"
        scenario_dir = tmp_path / case_label
        out_p = scenario_dir / f"{case_label}_out.log"
        err_p = scenario_dir / f"{case_label}_err.log"

        ready_proc: list[subprocess.Popen] = []
        spy_holder: list[SelectorSpy] = []

        def make_proc() -> subprocess.Popen:
            code = (
                "import sys\n"
                "sys.stdout.buffer.write(b'stream data\\n')\n"
                "sys.stdout.buffer.flush()\n"
            )
            p = _make_producer(code)
            ready_proc.append(p)
            return p

        def make_spy_selector(*a: Any, **k: Any) -> SelectorSpy:
            s = SelectorSpy(*a, **k)
            spy_holder.append(s)
            return s

        orig_read = os.read

        def spy_read(fd: int, length: int, sent: BaseException = sentinel) -> bytes:
            p = ready_proc[-1]
            if p.stdout is not None and fd == p.stdout.fileno():
                raise sent
            return orig_read(fd, length)

        def assert_error(err: BaseException, sent: BaseException = sentinel) -> None:
            assert err is sent

        def assert_evidence() -> None:
            assert len(spy_holder) == 1
            assert spy_holder[0].close_count == 1
            assert spy_holder[0].get_map() is None

        _run_fault_scenario(
            tmp_path,
            case_label,
            bad_data={"input_bytes": b"", "stdout_path": out_p, "stderr_path": err_p, "timeout": 5.0, "max_output": MAX_OUTPUT},
            make_bad_proc=make_proc,
            expected_exc_type=exc_type,
            custom_assert_error=assert_error,
            custom_assert_evidence=assert_evidence,
            patch_ctxs=[
                patch("os.read", side_effect=spy_read),
                patch("selectors.DefaultSelector", side_effect=make_spy_selector),
            ],
        )
