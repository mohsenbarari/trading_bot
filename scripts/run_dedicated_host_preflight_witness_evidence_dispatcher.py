#!/usr/bin/env python3
"""Unprivileged ForceCommand dispatcher for one Witness evidence read.

This is a different account and command from ordinary preflight receipt
collection.  It accepts no client arguments and no stdin selector.  Its only
allowed remote command is the literal Witness-evidence command rendered by
the receipt-agent boundary; it then invokes one no-argument root collector
through the distinct sudoers rule and emits only bounded canonical evidence.
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
from core.dedicated_host_preflight_ir_witness_attestation import (  # noqa: E402
    MAX_WA_IR_WITNESS_ATTESTATION_BYTES,
)


ROOT_COLLECTOR_TIMEOUT_SECONDS = 12
MAX_EVIDENCE_BYTES = MAX_WA_IR_WITNESS_ATTESTATION_BYTES * 2


class WitnessEvidenceDispatcherRuntimeError(RuntimeError):
    """Redacted local refusal that is never written to SSH stdout."""


def _fail() -> None:
    raise WitnessEvidenceDispatcherRuntimeError("witness evidence dispatcher rejected")


def _require_empty_stdin() -> bytes:
    """Refuse a selector/payload rather than silently consuming it."""

    try:
        payload = os.read(0, 1)
    except OSError:
        _fail()
    if payload:
        _fail()
    return b""


def _require_unprivileged_witness_evidence_account() -> tuple[str, int]:
    try:
        uid = os.getuid()
        effective_uid = os.geteuid()
        entry = pwd.getpwuid(effective_uid)
    except (KeyError, OSError):
        _fail()
    if (
        uid != effective_uid
        or effective_uid <= 0
        or entry.pw_name != boundary.FIXED_WITNESS_EVIDENCE_ACCOUNT
        or entry.pw_shell != boundary.FIXED_WITNESS_EVIDENCE_ACCOUNT_SHELL
    ):
        _fail()
    return entry.pw_name, effective_uid


def _require_root_controlled_source_layout() -> str:
    source_script = Path(__file__)
    try:
        resolved_script = source_script.resolve(strict=True)
    except OSError:
        _fail()
    if resolved_script != source_script:
        _fail()
    root = source_script.parents[1]
    try:
        dispatcher, _collector = boundary.witness_evidence_agent_source_paths(root.name)
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
        metadata = os.lstat(source_script)
    except OSError:
        _fail()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_size < 1
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


class _SubprocessSudoRunner(boundary.WitnessEvidenceAgentDispatcherRunner):
    """One exact, bounded no-shell sudo execution with no stdin payload."""

    @staticmethod
    def _validate_invocation(
        invocation: boundary.WitnessEvidenceAgentDispatcherInvocation,
    ) -> None:
        if type(invocation) is not boundary.WitnessEvidenceAgentDispatcherInvocation:
            _fail()
        try:
            _dispatcher, collector = boundary.witness_evidence_agent_source_paths(
                invocation.agent_release_sha
            )
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
            or invocation.stdin_bytes != b""
            or invocation.environment
            != (
                ("HOME", "/nonexistent"),
                ("LANG", "C"),
                ("LC_ALL", "C"),
                ("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"),
            )
        ):
            _fail()

    def run(
        self, *, invocation: boundary.WitnessEvidenceAgentDispatcherInvocation
    ) -> boundary.WitnessEvidenceAgentDispatcherRunnerResult:
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
            process.stdin.close()
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            payload = bytearray()
            deadline = time.monotonic() + ROOT_COLLECTOR_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    _fail()
                try:
                    chunk = os.read(
                        process.stdout.fileno(),
                        min(4096, MAX_EVIDENCE_BYTES + 1 - len(payload)),
                    )
                except OSError:
                    _fail()
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > MAX_EVIDENCE_BYTES:
                    _fail()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail()
            try:
                exit_code = process.wait(timeout=remaining)
            except (OSError, subprocess.TimeoutExpired):
                _fail()
            complete = True
            return boundary.WitnessEvidenceAgentDispatcherRunnerResult(
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
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    try:
        account_name, account_uid = _require_unprivileged_witness_evidence_account()
        release_sha = _require_root_controlled_source_layout()
        evidence = boundary.WitnessEvidenceAgentDispatcher(
            agent_release_sha=release_sha,
            runner=_SubprocessSudoRunner(),
        ).dispatch(
            original_command=_original_command(),
            arguments=arguments,
            account_name=account_name,
            account_uid=account_uid,
            stdin_bytes=_require_empty_stdin(),
        )
    except (WitnessEvidenceDispatcherRuntimeError, boundary.ReceiptAgentBoundaryError):
        return 2
    sys.stdout.buffer.write(evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
