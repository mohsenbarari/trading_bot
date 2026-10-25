"""Bound only local client output; normal payload files keep their normal limits."""
import json
import math
import os
import resource
import selectors
import signal
import stat
import subprocess
import time

LIMIT = 4 * 1024**2
MAX_SECONDS = 7200


def unknown(status):
    return json.dumps({"status": status, "retry_allowed": False,
        "daemon_cancellation_proven": False, "security_accepted": False},
        sort_keys=True).encode()


def _identity(fd):
    s = os.fstat(fd)
    if not stat.S_ISFIFO(s.st_mode):
        raise RuntimeError("client_pipe_unproved")
    return s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink


def _kill_owned(child):
    # start_new_session makes only this newly owned client's group the target.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=2)


def capture(argv, seconds, *, environment=None):
    """Return (local return code, bounded stdout); never return or hash stderr.

    Pipes also cover independently reopened stdout/stderr descriptors. Reads are
    limited to the remaining allowance plus one byte BEFORE accumulation. The
    extra byte detects overflow while exactly LIMIT bytes remain valid.
    """
    if (type(seconds) not in (int, float) or not math.isfinite(seconds)
            or not 0 < seconds <= MAX_SECONDS):
        raise ValueError("client_deadline")
    deadline = time.monotonic() + seconds
    cpu = math.ceil(seconds) + 5
    child = None
    selector = None
    streams = []
    stdout = bytearray()
    counts = {"stdout": 0, "stderr": 0}
    outcome = None

    def limits():
        # No RLIMIT_FSIZE or RLIMIT_AS: gzip and backup files are payloads.
        _, hard = resource.getrlimit(resource.RLIMIT_CPU)
        allowed = cpu if hard == resource.RLIM_INFINITY else min(cpu, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (allowed, allowed))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    try:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
            start_new_session=True, preexec_fn=limits)
        streams = [(child.stdout, None), (child.stderr, None)]
        selector = selectors.DefaultSelector()
        for index, (name, stream) in enumerate((("stdout", child.stdout), ("stderr", child.stderr))):
            proof = _identity(stream.fileno())
            streams[index] = (stream, proof)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = (124, unknown("process_timeout_unknown"))
                break
            for key, _ in selector.select(min(remaining, .1)):
                name = key.data
                block = os.read(key.fd, min(65536, LIMIT - counts[name] + 1))
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                counts[name] += len(block)
                if counts[name] > LIMIT:
                    outcome = (125, unknown("process_" + name + "_limit_unknown"))
                    break
                if name == "stdout":
                    stdout.extend(block)
                # stderr's bounded block is discarded, never retained/returned.
                del block
            if outcome is not None:
                break
        if outcome is None:
            try:
                code = child.wait(timeout=max(.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                outcome = (124, unknown("process_timeout_unknown"))
            else:
                # A surviving group is an unknown local client tree, not success.
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    outcome = (code, bytes(stdout))
                else:
                    outcome = (125, unknown("client_descendants_unknown"))
        if outcome[0] in (124, 125):
            _kill_owned(child)
        for stream, proof in streams:
            if _identity(stream.fileno()) != proof:
                raise RuntimeError("client_pipe_unproved")
    except Exception:
        outcome = (125, unknown("client_capture_unproved"))
        if child is not None:
            try:
                _kill_owned(child)
            except Exception:
                outcome = (125, unknown("client_cleanup_unproved"))
    finally:
        failed_close = False
        if child is not None and child.poll() is None:
            try:
                _kill_owned(child)
            except Exception:
                failed_close = True
        if selector is not None:
            try:
                selector.close()
            except Exception:
                failed_close = True
        for stream, _ in streams:
            try:
                stream.close()
            except Exception:
                failed_close = True
        if failed_close:
            outcome = (125, unknown("client_cleanup_unproved"))
    return outcome
