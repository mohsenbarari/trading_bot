"""Closed generation lifecycle facts; no Provider, acceptance or execution authority.

Runner alone opts into this policy with a task/schema-bound pure frame checker.
Default shared capture callers do not use it. Historical OS facts are observations,
not facts a later persisted reader can independently re-observe.
"""
from __future__ import annotations

import hashlib
import math
import os
import signal
import subprocess
import time
from typing import Callable

POLICY = "FINAL_FRAME_RECOVERY_V1"
POST_FINAL_GRACE_SECONDS = 5.0
OUTCOME_NORMAL = "NORMAL_EXIT"
OUTCOME_RECOVERED = "RECOVERED_COMPLETED_RESULT"


class GenerationCompletionError(ValueError):
    """Fixed lifecycle failure without retaining Provider/source payload."""


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise GenerationCompletionError(message)


def _stream_facts(input_bytes: bytes, stdout: bytes, stderr: bytes) -> dict:
    return {
        "input_bytes": len(input_bytes), "input_sha256": digest(input_bytes),
        "stdout_bytes": len(stdout), "stdout_sha256": digest(stdout),
        "stderr_bytes": len(stderr), "stderr_sha256": digest(stderr),
    }


def normal_observations(
    actual_returncode: int, input_bytes: bytes, stdout: bytes, stderr: bytes,
    overall_timeout_seconds: float,
) -> dict:
    """Called only after trusted capture returned normally and observed exit0.

    The existing capture return contract guarantees complete write/owned-writer
    close, both pipe EOFs and child wait. This is not backend-ingestion evidence.
    """
    _need(type(actual_returncode) is int and actual_returncode == 0,
          "normal completion requires actual exit0")
    return {
        "outcome": OUTCOME_NORMAL, "normal_exit_observed": True,
        "actual_returncode": actual_returncode, "shutdown_timeout_observed": False,
        "timeout_kind": None, "cleanup_signal": None, "signal_attempted": False,
        "direct_child_reaped": True, "owned_group_absent": None,
        "stdout_eof": True, "stderr_eof": True,
        "full_input_written": True, "input_writer_closed": True,
        "observed_close_before_terminal_read": None,
        "lifecycle_within_capture_deadline": True,
        "overall_timeout_seconds": float(overall_timeout_seconds),
        "post_final_grace_seconds": None,
        **_stream_facts(input_bytes, stdout, stderr),
    }


class GenerationRecoveryPolicy:
    """Per-invocation observer. Never installed globally or selected by env."""

    def __init__(self, frame_check: Callable[[bytes, bytes], bool]) -> None:
        _need(callable(frame_check), "generation frame checker is required")
        self._check = frame_check
        self._input = b""
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._line = bytearray()
        self._terminal_seen = False
        self._closed_at: float | None = None
        self._terminal_read_at: float | None = None
        self._early_terminal_read = False
        self._eligible_at: float | None = None
        self._pgid: int | None = None
        self._signal_attempted = False
        self._signal_sent = False
        self._timeout = 0.0
        self._deadline = 0.0
        self._started = False
        self.failure_facts: dict | None = None

    def _observed_state(self, process: subprocess.Popen, *, written: int,
                        stdout_eof: bool, stderr_eof: bool) -> dict:
        # Bounded diagnostics only. Never infer the historical pre-cleanup
        # return code from a later reap, or PGID absence from parent absence.
        try:
            returncode = process.poll()
            poll_error = None
        except BaseException as exc:
            returncode, poll_error = None, type(exc).__name__
        group_absent: bool | None = None
        group_error = None
        if self._pgid is not None:
            try:
                os.killpg(self._pgid, 0)
                group_absent = False
            except ProcessLookupError:
                group_absent = True
            except BaseException as exc:
                group_error = type(exc).__name__
        observed = time.monotonic()
        return {
            "parent_poll_returncode": returncode,
            "parent_poll_error_type": poll_error,
            "owned_pgid": self._pgid,
            "owned_group_absent": group_absent,
            "group_probe_error_type": group_error,
            "stdout_eof_observed": stdout_eof,
            "stderr_eof_observed": stderr_eof,
            "input_written_bytes": written,
            "input_expected_bytes": len(self._input),
            "input_writer_close_observed": self._closed_at is not None,
            "terminal_read_observed": self._terminal_read_at is not None,
            "signal_request_attempted": self._signal_attempted,
            "signal_request_returned": self._signal_sent,
            "capture_deadline_reached": observed >= self._deadline,
            "seconds_before_capture_deadline": self._deadline - observed,
            **_stream_facts(self._input, bytes(self._stdout), bytes(self._stderr)),
        }

    def failure_before_cleanup(self, process: subprocess.Popen, *, written: int,
                               stdout_eof: bool, stderr_eof: bool) -> None:
        self.failure_facts = {
            "kind": "GENERATION_CAPTURE_FAILURE_OBSERVATIONS_V1",
            "eligible": False,
            "output_digest_domain": "CAPTURE_ACCEPTED_FILE_WRITES_NOT_POSTCLOSE_READBACK",
            "before_cleanup": self._observed_state(
                process, written=written, stdout_eof=stdout_eof,
                stderr_eof=stderr_eof,
            ),
            "after_cleanup": None,
        }

    def failure_after_cleanup(self, process: subprocess.Popen, *, written: int,
                              stdout_eof: bool, stderr_eof: bool) -> None:
        if self.failure_facts is not None:
            self.failure_facts["after_cleanup"] = self._observed_state(
                process, written=written, stdout_eof=stdout_eof,
                stderr_eof=stderr_eof,
            )

    def cleanup_owned_group_on_failure(self) -> None:
        """Best-effort bounded containment, never a completion eligibility path.

        The direct child can already be reaped while its original owned group
        still holds a pipe. Do not use parent absence as group absence, and do
        not discover or signal unrelated PIDs or unpinned groups.
        """
        if self._pgid is not None:
            try:
                os.killpg(self._pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def start(self, process: subprocess.Popen, input_bytes: bytes,
              timeout: float, deadline: float) -> None:
        _need(not self._started, "generation observer reused")
        self._started = True
        self._input, self._timeout, self._deadline = input_bytes, float(timeout), deadline
        # A very fast naturally exited child is still a valid default capture;
        # missing PGID here disables recovery rather than changing that path.
        try:
            group = os.getpgid(process.pid)
        except ProcessLookupError:
            group = None
        if group == process.pid and type(group) is int and group > 0:
            self._pgid = group

    def input_closed(self, written: int) -> None:
        if written == len(self._input):
            self._closed_at = time.monotonic()

    def observe_stdout(self, chunk: bytes) -> None:
        self._stdout.extend(chunk)
        self._line.extend(chunk)
        # Incremental framing: each line is decoded once; copy/strict-parse the
        # full stream only on a complete terminal candidate and after cleanup.
        while True:
            end = self._line.find(b"\n")
            if end < 0:
                break
            candidate = bytes(self._line[:end])
            del self._line[:end + 1]
            if not candidate.strip():
                continue
            if self._terminal_seen:
                self._eligible_at = None
                continue
            try:
                import json
                event = json.loads(candidate)
            except (UnicodeError, ValueError):
                event = None
            if isinstance(event, dict) and event.get("event") == "result":
                self._terminal_seen = True
                observed = time.monotonic()
                if self._closed_at is None:
                    self._early_terminal_read = True
                else:
                    self._terminal_read_at = observed
                valid = (
                    not self._stderr and self._closed_at is not None
                    and not self._early_terminal_read
                    and self._check(bytes(self._stdout), bytes(self._stderr))
                )
                if valid:
                    self._eligible_at = time.monotonic()
        if self._terminal_seen and self._line.strip():
            self._eligible_at = None

    def observe_stderr(self, chunk: bytes) -> None:
        self._stderr.extend(chunk)
        if chunk:
            self._eligible_at = None

    def observe_failed_prefix(self, chunk: bytes, *, stdout: bool) -> None:
        # Account the physically accepted overflow prefix without making it an
        # eligibility candidate. Failure digests have an explicit write domain.
        (self._stdout if stdout else self._stderr).extend(chunk)
        self._eligible_at = None

    def _remaining(self) -> float:
        return self._deadline - time.monotonic()

    def next_wait(self, ordinary_remaining: float) -> float:
        if self._eligible_at is None or self._signal_attempted or self._pgid is None:
            return ordinary_remaining
        return min(ordinary_remaining, max(
            0.0, self._eligible_at + POST_FINAL_GRACE_SECONDS - time.monotonic()
        ))

    def maybe_shutdown(self, process: subprocess.Popen) -> None:
        if (self._eligible_at is None or self._signal_attempted or self._pgid is None
                or time.monotonic() < self._eligible_at + POST_FINAL_GRACE_SECONDS):
            return
        if process.poll() is not None:
            self._eligible_at = None
            return  # Natural nonzero remains a failure in Runner.
        _need(self._remaining() > 0, "generation cleanup deadline exhausted")
        self._signal_attempted = True
        try:
            os.killpg(self._pgid, signal.SIGKILL)
            self._signal_sent = True
        except ProcessLookupError:
            # A natural-exit/signal race is NOT relabelled as recovered success.
            return
        remaining = self._remaining()
        _need(remaining > 0, "generation cleanup deadline exhausted")
        process.wait(timeout=min(10.0, remaining))

    def wait_after_pipe_eof(self, process: subprocess.Popen) -> None:
        while process.poll() is None:
            self.maybe_shutdown(process)
            if process.poll() is not None:
                break
            remaining = self._remaining()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(getattr(process, "args", "CLI"), self._timeout)
            wait = self.next_wait(remaining)
            # Zero means a grace boundary, not a new unbounded wait.
            if wait <= 0:
                self.maybe_shutdown(process)
                if process.poll() is None:
                    wait = min(0.01, remaining)
                else:
                    break
            try:
                process.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                if self._remaining() <= 0:
                    raise subprocess.TimeoutExpired(getattr(process, "args", "CLI"), self._timeout)
        # Always reap the direct child, even when poll already observed exit.
        remaining = self._remaining()
        _need(remaining > 0, "generation cleanup deadline exhausted")
        process.wait(timeout=remaining)

    def finish(self, process: subprocess.Popen, *, stdout_eof: bool,
               stderr_eof: bool, written: int) -> dict | None:
        if not self._signal_attempted:
            return None
        actual_returncode = process.returncode
        _need(self._signal_sent and type(actual_returncode) is int
              and actual_returncode in (-signal.SIGKILL, 0),
              "generation shutdown exit race is ambiguous")
        _need(stdout_eof and stderr_eof and written == len(self._input)
              and self._closed_at is not None and self._terminal_read_at is not None
              and self._closed_at <= self._terminal_read_at
              and not self._early_terminal_read and not self._stderr,
              "generation recovery has incomplete lifecycle evidence")
        _need(self._check(bytes(self._stdout), bytes(self._stderr)),
              "generation cleanup tail invalidates final result")
        _need(self._pgid is not None, "generation process group was not pinned")
        while True:
            _need(self._remaining() > 0, "generation group quiescence deadline exhausted")
            try:
                os.killpg(self._pgid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                raise GenerationCompletionError("generation group absence is unknown") from None
            remaining = self._remaining()
            _need(remaining > 0, "generation group quiescence deadline exhausted")
            time.sleep(min(0.01, remaining))
        _need(self._remaining() > 0, "generation group quiescence deadline exhausted")
        return {
            "outcome": OUTCOME_RECOVERED, "normal_exit_observed": False,
            "actual_returncode": actual_returncode, "shutdown_timeout_observed": True,
            # A returned signal request does not prove it caused child exit.
            # Keep race observations recovered, never claim ordinary natural exit.
            "timeout_kind": ("POST_FINAL_EXIT_RACE" if actual_returncode == 0
                             else "POST_FINAL_SHUTDOWN"),
            "cleanup_signal": int(signal.SIGKILL),
            "signal_attempted": True, "direct_child_reaped": True,
            "owned_group_absent": True, "stdout_eof": True, "stderr_eof": True,
            "full_input_written": True, "input_writer_closed": True,
            "observed_close_before_terminal_read": True,
            "lifecycle_within_capture_deadline": True,
            "overall_timeout_seconds": self._timeout,
            "post_final_grace_seconds": POST_FINAL_GRACE_SECONDS,
            **_stream_facts(self._input, bytes(self._stdout), bytes(self._stderr)),
        }


def validate_observations(value: object, input_bytes: bytes, stdout: bytes,
                          stderr: bytes) -> dict:
    """Check retained typed consistency, NOT historical OS re-observation."""
    _need(type(value) is dict, "completion observations must be a mapping")
    reference = normal_observations(0, input_bytes, stdout, stderr, 1.0)
    _need(set(value) == set(reference), "completion observations have unexpected keys")
    for key, expected in _stream_facts(input_bytes, stdout, stderr).items():
        _need(type(value[key]) is type(expected) and value[key] == expected,
              "completion retained bytes mismatch")
    for key in ("direct_child_reaped", "stdout_eof", "stderr_eof",
                "full_input_written", "input_writer_closed",
                "lifecycle_within_capture_deadline"):
        _need(type(value[key]) is bool and value[key] is True,
              "completion lifecycle is incomplete")
    timeout = value["overall_timeout_seconds"]
    _need(type(timeout) in (int, float) and 0 < timeout <= 915 and math.isfinite(timeout),
          "completion timeout is invalid")
    if value["outcome"] == OUTCOME_NORMAL:
        expected = normal_observations(0, input_bytes, stdout, stderr, timeout)
        _need(all(type(value[key]) is type(item) and value[key] == item
                  for key, item in expected.items()),
              "normal completion did not observe natural exit0")
    elif value["outcome"] == OUTCOME_RECOVERED:
        _need(
            type(value["normal_exit_observed"]) is bool and value["normal_exit_observed"] is False
            and type(value["actual_returncode"]) is int
            and ((value["actual_returncode"] == -signal.SIGKILL
                  and value["timeout_kind"] == "POST_FINAL_SHUTDOWN")
                 or (value["actual_returncode"] == 0
                     and value["timeout_kind"] == "POST_FINAL_EXIT_RACE"))
            and type(value["cleanup_signal"]) is int
            and value["cleanup_signal"] == signal.SIGKILL
            and type(value["post_final_grace_seconds"]) is float
            and value["post_final_grace_seconds"] == POST_FINAL_GRACE_SECONDS
            and not stderr,
            "recovered completion has invalid shutdown observations",
        )
        for key in ("shutdown_timeout_observed", "signal_attempted",
                    "owned_group_absent", "observed_close_before_terminal_read"):
            _need(type(value[key]) is bool and value[key] is True,
                  "recovered completion has incomplete shutdown observations")
    else:
        raise GenerationCompletionError("unknown completion outcome")
    return dict(value)

def capture_generation_process(
    process: subprocess.Popen, input_bytes: bytes, stdout_path, stderr_path,
    timeout: float, max_output: int, policy: GenerationRecoveryPolicy, *,
    error_type: type[Exception], terminate: Callable[[subprocess.Popen], None],
    stream_chunk_size: int,
) -> dict | None:
    """Opt-in loop only; default capture's historical ordering stays in Runner."""
    import selectors

    selector = None
    outputs = []
    streams = {}
    eof = {process.stdout: False, process.stderr: False}
    written = total = 0
    primary: BaseException | None = None
    result = None
    process_cleanup_attempted = False

    def remember(exc: BaseException) -> None:
        nonlocal primary
        if primary is None:
            primary = exc

    def diagnose(before: bool) -> None:
        try:
            function = policy.failure_before_cleanup if before else policy.failure_after_cleanup
            function(process, written=written, stdout_eof=eof[process.stdout],
                     stderr_eof=eof[process.stderr])
        except BaseException:
            pass  # Bounded best-effort telemetry cannot replace the first error.

    def clean_process() -> None:
        nonlocal process_cleanup_attempted
        if process_cleanup_attempted:
            return
        process_cleanup_attempted = True
        diagnose(True)
        try:
            policy.cleanup_owned_group_on_failure()
        except BaseException as exc:
            remember(exc)
        try:
            terminate(process)
        except BaseException as exc:
            remember(exc)
        try:
            # terminate's historical fast return for an exited parent is not
            # itself a reap/descendant observation. Reap our direct child only.
            process.wait(timeout=10)
        except BaseException as exc:
            remember(exc)
        diagnose(False)

    try:
        # Exact policy validation happens before this function allocates anything.
        selector = selectors.DefaultSelector()
        deadline = time.monotonic() + timeout
        policy.start(process, input_bytes, timeout, deadline)
        stdout_file = stdout_path.open("xb")
        outputs.append(stdout_file)
        stderr_file = stderr_path.open("xb")
        outputs.append(stderr_file)
        streams = {process.stdout: stdout_file, process.stderr: stderr_file}
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, "output")
        os.set_blocking(process.stdin.fileno(), False)
        if input_bytes:
            selector.register(process.stdin, selectors.EVENT_WRITE, "input")
        else:
            process.stdin.close()
            policy.input_closed(0)
        while selector.get_map():
            policy.maybe_shutdown(process)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(getattr(process, "args", "CLI"), timeout)
            wait = max(0.0, policy.next_wait(min(remaining, 0.25)))
            events = selector.select(wait)
            if not events and process.poll() is not None and not process.stdin.closed:
                try:
                    selector.get_key(process.stdin)
                except KeyError:
                    pass
                else:
                    selector.unregister(process.stdin)
                    process.stdin.close()
                    continue
            for key, _mask in events:
                stream = key.fileobj
                if key.data == "input":
                    try:
                        count = os.write(stream.fileno(), input_bytes[written:])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    written += count
                    if written == len(input_bytes):
                        selector.unregister(stream)
                        stream.close()
                        policy.input_closed(written)
                    continue
                remaining_output = max_output - total
                try:
                    chunk = os.read(stream.fileno(), min(
                        stream_chunk_size, remaining_output + 1,
                    ))
                except BlockingIOError:
                    continue
                if not chunk:
                    eof[stream] = True
                    selector.unregister(stream)
                    stream.close()
                    continue
                if len(chunk) > remaining_output:
                    if remaining_output:
                        prefix = chunk[:remaining_output]
                        streams[stream].write(prefix)
                        total += len(prefix)
                        policy.observe_failed_prefix(prefix, stdout=stream is process.stdout)
                    raise error_type("CLI output exceeds budget")
                streams[stream].write(chunk)
                total += len(chunk)
                if stream is process.stdout:
                    policy.observe_stdout(chunk)
                else:
                    policy.observe_stderr(chunk)
        policy.wait_after_pipe_eof(process)
        if written != len(input_bytes):
            raise error_type("CLI input was not completely written")
        result = policy.finish(process, stdout_eof=eof[process.stdout],
                               stderr_eof=eof[process.stderr], written=written)
    except BaseException as exc:
        remember(exc)
    finally:
        # Unlike nested file contexts, latch the body/open error before any close.
        # Attempt every owned close even when an earlier close/cleanup fails.
        if primary is not None:
            clean_process()
        for output in reversed(outputs):
            try:
                output.close()
            except BaseException as exc:
                remember(exc)
        if selector is not None:
            try:
                selector.close()
            except BaseException as exc:
                remember(exc)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except BaseException as exc:
                remember(exc)
        if primary is not None:
            clean_process()
    if primary is not None:
        raise primary
    return result
