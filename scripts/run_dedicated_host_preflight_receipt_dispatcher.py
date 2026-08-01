#!/usr/bin/env python3
"""Unprivileged SSH ForceCommand dispatcher for one read-only receipt.

This program is intended to be invoked only by the rendered ``sshd`` Match
block for the ``preflight`` account.  It has no command-line interface and
never opens the root-only runtime configuration.  It accepts one canonical
bounded request from stdin, then calls exactly one root collector through the
rendered no-argument sudoers rule.  It emits no diagnostic stdout; success is
the one canonical redacted receipt returned by that collector.

The source release directory, Python interpreter, sudo binary, command name,
environment, timeout, and collector path are all fixed by the local contract.
WA-IR is categorically rejected before sudo is invoked.
"""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import selectors
import stat
import subprocess
import sys
import time
from typing import Sequence


sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from core import dedicated_host_preflight_receipt_agent_boundary as boundary  # noqa: E402
from core.dedicated_host_preflight_receipt import MAX_RECEIPT_BYTES  # noqa: E402


MAX_REQUEST_BYTES = 4 * 1024
ROOT_COLLECTOR_TIMEOUT_SECONDS = 12


class ReceiptAgentDispatcherRuntimeError(RuntimeError):
    """An internal redacted dispatcher failure; it is never printed."""


def _fail() -> None:
    raise ReceiptAgentDispatcherRuntimeError("receipt-agent dispatcher rejected")


def _read_bounded_stdin() -> bytes:
    payload = bytearray()
    while len(payload) <= MAX_REQUEST_BYTES:
        try:
            chunk = os.read(0, min(4096, MAX_REQUEST_BYTES + 1 - len(payload)))
        except OSError:
            _fail()
        if not chunk:
            break
        payload.extend(chunk)
    if not 1 <= len(payload) <= MAX_REQUEST_BYTES:
        _fail()
    return bytes(payload)


def _require_unprivileged_preflight_account() -> tuple[str, int]:
    try:
        uid = os.getuid()
        effective_uid = os.geteuid()
        entry = pwd.getpwuid(effective_uid)
    except (KeyError, OSError):
        _fail()
    if (
        uid != effective_uid
        or effective_uid <= 0
        or entry.pw_name != boundary.FIXED_PREFLIGHT_ACCOUNT
        or entry.pw_shell != boundary.FIXED_PREFLIGHT_ACCOUNT_SHELL
    ):
        _fail()
    return entry.pw_name, effective_uid


def _require_root_controlled_source_layout() -> str:
    """Bind this program to the one source-pinned immutable release tree."""

    source_script = Path(__file__)
    try:
        resolved_script = source_script.resolve(strict=True)
    except OSError:
        _fail()
    if resolved_script != source_script:
        _fail()
    root = source_script.parents[1]
    try:
        dispatcher, _collector, _readonly = boundary.agent_source_paths(root.name)
    except boundary.ReceiptAgentBoundaryError:
        _fail()
    if root.parent != boundary.FIXED_PREFLIGHT_AGENT_RELEASES_ROOT or dispatcher != source_script:
        _fail()

    current = Path("/")
    for component in (*root.parts[1:], "scripts"):
        current = current / component
        try:
            metadata = os.lstat(current)
        except OSError:
            _fail()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            _fail()
    try:
        file_metadata = os.lstat(source_script)
    except OSError:
        _fail()
    if (
        stat.S_ISLNK(file_metadata.st_mode)
        or not stat.S_ISREG(file_metadata.st_mode)
        or file_metadata.st_uid != 0
        or file_metadata.st_nlink != 1
        or stat.S_IMODE(file_metadata.st_mode) & 0o022
        or file_metadata.st_size < 1
    ):
        _fail()
    return root.name


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.kill()
    except OSError:
        return
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


class _SubprocessSudoRunner(boundary.ReceiptAgentDispatcherRunner):
    """One bounded, clean-environment no-shell runner for the exact sudo argv."""

    @staticmethod
    def _validate_invocation(
        invocation: boundary.ReceiptAgentDispatcherInvocation,
    ) -> None:
        if type(invocation) is not boundary.ReceiptAgentDispatcherInvocation:
            _fail()
        try:
            _dispatcher, collector, _readonly = boundary.agent_source_paths(
                invocation.agent_release_sha
            )
            request = boundary.parse_receipt_agent_request_payload(invocation.stdin_bytes)
        except boundary.ReceiptAgentBoundaryError:
            _fail()
        expected = (
            str(boundary.FIXED_PREFLIGHT_SUDO_BINARY),
            "-n",
            "-u",
            "root",
            "--",
            str(boundary.FIXED_PREFLIGHT_SYSTEM_PYTHON),
            "-I",
            str(collector),
        )
        if (
            invocation.arguments != expected
            or invocation.environment
            != (
                ("HOME", "/nonexistent"),
                ("LANG", "C"),
                ("LC_ALL", "C"),
                ("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"),
            )
            or request.role != invocation.request_role
        ):
            _fail()

    def run(
        self, *, invocation: boundary.ReceiptAgentDispatcherInvocation
    ) -> boundary.ReceiptAgentDispatcherRunnerResult:
        self._validate_invocation(invocation)
        process: subprocess.Popen[bytes] | None = None
        selector: selectors.BaseSelector | None = None
        complete = False
        try:
            process = subprocess.Popen(
                invocation.arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd="/",
                env=dict(invocation.environment),
                close_fds=True,
                start_new_session=True,
                shell=False,
            )
            if process.stdin is None or process.stdout is None:
                _fail()
            process.stdin.write(invocation.stdin_bytes)
            process.stdin.close()
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            payload = bytearray()
            deadline = time.monotonic() + ROOT_COLLECTOR_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _fail()
                if not selector.select(remaining):
                    _fail()
                try:
                    chunk = os.read(
                        process.stdout.fileno(),
                        min(4096, MAX_RECEIPT_BYTES + 1 - len(payload)),
                    )
                except OSError:
                    _fail()
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > MAX_RECEIPT_BYTES:
                    _fail()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail()
            try:
                exit_code = process.wait(timeout=remaining)
            except (OSError, subprocess.TimeoutExpired):
                _fail()
            complete = True
            return boundary.ReceiptAgentDispatcherRunnerResult(
                exit_code=exit_code,
                stdout_bytes=bytes(payload),
            )
        except (OSError, ValueError, BrokenPipeError):
            _fail()
        finally:
            if selector is not None:
                selector.close()
            if process is not None and not complete:
                _terminate(process)


def _original_command() -> str:
    value = os.environ.get("SSH_ORIGINAL_COMMAND")
    if type(value) is not str:
        _fail()
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Run the one forced exchange.  Any fault has no stdout diagnostic."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    try:
        account_name, account_uid = _require_unprivileged_preflight_account()
        release_sha = _require_root_controlled_source_layout()
        dispatcher = boundary.ReceiptAgentDispatcher(
            agent_release_sha=release_sha,
            runner=_SubprocessSudoRunner(),
        )
        receipt = dispatcher.dispatch(
            original_command=_original_command(),
            arguments=arguments,
            account_name=account_name,
            account_uid=account_uid,
            request_bytes=_read_bounded_stdin(),
        )
    except (ReceiptAgentDispatcherRuntimeError, boundary.ReceiptAgentBoundaryError):
        return 2
    sys.stdout.buffer.write(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
