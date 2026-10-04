"""Bounded CLI capture primitive for two-site automation."""

from __future__ import annotations

import os
from pathlib import Path
import selectors
import subprocess
import time

MAX_OUTPUT: int = 2_000_000


def capture_bounded_process(
    process: subprocess.Popen,
    input_bytes: bytes,
    stdout_path: Path,
    stderr_path: Path,
    timeout: float,
    max_output: int = MAX_OUTPUT,
) -> None:
    """Drain all pipes concurrently while enforcing one hard stdout+stderr cap."""
    from scripts.two_site_proposal_runner import (
        STREAM_CHUNK_SIZE,
        ProposalError,
        terminate_process,
    )

    if max_output < 1 or process.stdin is None or process.stdout is None or process.stderr is None:
        raise ProposalError("invalid bounded process configuration")
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    input_offset = 0
    total_output = 0
    streams = {}

    try:
        with stdout_path.open("xb") as stdout_file, stderr_path.open("xb") as stderr_file:
            streams = {process.stdout: stdout_file, process.stderr: stderr_file}
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, "output")
            os.set_blocking(process.stdin.fileno(), False)
            if input_bytes:
                selector.register(process.stdin, selectors.EVENT_WRITE, "input")
            else:
                process.stdin.close()

            while selector.get_map():
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise subprocess.TimeoutExpired(getattr(process, "args", "CLI"), timeout)
                events = selector.select(min(remaining_time, 0.25))
                if not events and process.poll() is not None:
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
                            written = os.write(stream.fileno(), input_bytes[input_offset:])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            selector.unregister(stream)
                            stream.close()
                            continue
                        input_offset += written
                        if input_offset == len(input_bytes):
                            selector.unregister(stream)
                            stream.close()
                        continue

                    remaining_output = max_output - total_output
                    try:
                        chunk = os.read(
                            stream.fileno(),
                            min(STREAM_CHUNK_SIZE, remaining_output + 1),
                        )
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    if len(chunk) > remaining_output:
                        if remaining_output:
                            streams[stream].write(chunk[:remaining_output])
                            total_output += remaining_output
                        raise ProposalError("CLI output exceeds budget")
                    streams[stream].write(chunk)
                    total_output += len(chunk)

            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise subprocess.TimeoutExpired(getattr(process, "args", "CLI"), timeout)
            process.wait(timeout=remaining_time)
    except BaseException:
        terminate_process(process)
        raise
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
