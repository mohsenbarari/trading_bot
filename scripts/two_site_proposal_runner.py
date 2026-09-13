#!/usr/bin/env python3
"""Bounded Antigravity proposals. Only Codex applies/reviews/commits them.

The controller writes ignored run artifacts only; it does not apply generated code,
change Git refs/Stage state or deploy. The CLI uses plan/sandbox mode and a deny hook;
tool attempts invalidate its result. Each invocation is a fresh session.
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from typing import Iterator

try:
    from scripts.two_site_cli_artifact import prepare_disposable_cli
except ImportError:
    from two_site_cli_artifact import prepare_disposable_cli

try:
    from scripts.two_site_assignment_scope import (
        AssignmentScopeError,
        load_expected_assignment_scope,
        validate_task_scope,
    )
except ImportError:
    from two_site_assignment_scope import (
        AssignmentScopeError,
        load_expected_assignment_scope,
        validate_task_scope,
    )

try:
    from scripts.two_site_proposal_transport import (
        TransportPolicyError,
        build_changes_schema,
        validate_change_mode,
    )
except ImportError:
    from two_site_proposal_transport import (
        TransportPolicyError,
        build_changes_schema,
        validate_change_mode,
    )

try:
    from scripts.two_site_correction_delta import (
        CorrectionDeltaError,
        build_candidate_sources,
        rebase_correction_result,
    )
except ImportError:
    from two_site_correction_delta import (
        CorrectionDeltaError,
        build_candidate_sources,
        rebase_correction_result,
    )

try:
    from scripts.two_site_pre_review_receipt import validate_test_receipt
except ImportError:
    from two_site_pre_review_receipt import validate_test_receipt

ROOT = Path(__file__).resolve().parents[1]
MAX_CONTEXT = 160_000
MAX_OUTPUT = 2_000_000
STREAM_CHUNK_SIZE = 64 * 1024
GEMINI_RUNTIME_CONTRACT_REL = Path(
    "docs/refactor/two-server/templates/gemini-runtime-contract.json"
)
GEMINI_RUNTIME_REFERENCE_PATHS = (
    Path("docs/refactor/two-server/PROPOSAL_AUTOMATION.md"),
    Path("docs/refactor/two-server/ANTIGRAVITY_EXECUTION.md"),
    Path(".agents/skills/two-site-refactor/resources/AUTONOMOUS_ORCHESTRATION.md"),
    Path(".agents/skills/two-site-refactor/resources/TYPED_GATEWAY.md"),
)
RUNTIME_CONTRACT_KEYS = frozenset({
    "schema_version", "provider", "model", "effort", "worker_model", "worker_effort",
    "cli_version", "cli_sha256",
})
VALID_EFFORTS = frozenset({"low", "medium", "high"})
DENY_HOOK = {"proposal-only": {"PreToolUse": [{"matcher": "*", "hooks": [{
    "type": "command", "timeout": 5,
    "command": "/usr/bin/printf '%s\\n' '{\"decision\":\"deny\",\"reason\":\"Proposal-only session: tools are disabled. Return JSON directly from supplied context.\"}'",
}]}]}}
SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"\d{8,12}:[A-Za-z0-9_-]{30,})\b"
)
PROTECTED = {".git", ".local", ".agents", ".codex", ".cursor", "data", "logs",
             "uploads", "secrets", "credentials", "backups"}
CANONICAL_RESPONSE_KEYS = frozenset({
    "status", "summary", "covered_requirements", "findings", "changes",
})
MAX_REPLACEMENTS = 32
MAX_REPLACEMENT_BYTES = 65536
MAX_REVIEW_TRANSPORT_BYTES = MAX_CONTEXT
AUTOPILOT_RUN_ID_ENV = "TWO_SITE_AUTOPILOT_RUN_ID"
MAX_AUTOPILOT_RUN_ID_LENGTH = 64
AUTOPILOT_RUN_ID_REGEX = re.compile(
    r"^cycle-[0-9]{4,8}-[0-9]{8}T[0-9]{6}Z-[0-9]{1,10}\Z"
)


def validate_autopilot_run_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ProposalError("invalid autopilot run ID: empty or whitespace")
    if len(raw) > MAX_AUTOPILOT_RUN_ID_LENGTH:
        raise ProposalError("invalid autopilot run ID: exceeds length limit")
    if not AUTOPILOT_RUN_ID_REGEX.fullmatch(raw):
        raise ProposalError("invalid autopilot run ID: does not match canonical format")
    if SECRET.search(raw):
        raise ProposalError("invalid autopilot run ID: credential-like content")
    return raw


class ProposalError(ValueError):
    pass


class RetryableProviderSessionError(ProposalError):
    pass


class RetryableProviderOutputError(RetryableProviderSessionError):
    """A bounded-retry failure attributable only to one provider response."""


_RETRYABLE_OUTPUT_MESSAGES = frozenset({
    "incomplete response contract",
    "malformed response list",
    "plan-only or no-op response is not implementation",
    "invalid change shape",
    "mixed change content and replacements",
    "empty change content and replacements",
    "proposal transport policy violation",
    "new-file replacement is not supported",
    "replacements must be a list",
    "replacement item must be a dict with exactly 'old' and 'new'",
    "replacement old must be a nonempty string",
    "replacement new must be a string",
    "replacement old and new must differ",
    "aggregate replacement UTF-8 bytes exceed limit",
    "replacement spans overlap",
    "empty or unchanged file",
})


def _provider_output_is_retryable(exc: ProposalError) -> bool:
    message = str(exc)
    return (
        message in _RETRYABLE_OUTPUT_MESSAGES
        or message.startswith("replacements count must be between ")
        or message.startswith("replacement target not found in original:")
        or message.startswith("replacement target occurs more than once in original:")
    )


def materialize_replacements(original: str, replacements: object) -> str:
    if not isinstance(original, str):
        raise ProposalError("original text must be a string")
    if not isinstance(replacements, list):
        raise ProposalError("replacements must be a list")
    if not (1 <= len(replacements) <= MAX_REPLACEMENTS):
        raise ProposalError(f"replacements count must be between 1 and {MAX_REPLACEMENTS}")

    total_bytes = 0
    spans = []
    for item in replacements:
        if not isinstance(item, dict) or set(item.keys()) != {"old", "new"}:
            raise ProposalError("replacement item must be a dict with exactly 'old' and 'new'")
        old = item["old"]
        new = item["new"]
        if not isinstance(old, str) or not old:
            raise ProposalError("replacement old must be a nonempty string")
        if not isinstance(new, str):
            raise ProposalError("replacement new must be a string")
        if old == new:
            raise ProposalError("replacement old and new must differ")

        total_bytes += len(old.encode("utf-8")) + len(new.encode("utf-8"))
        if total_bytes > MAX_REPLACEMENT_BYTES:
            raise ProposalError("aggregate replacement UTF-8 bytes exceed limit")

        start = original.find(old)
        if start == -1:
            raise ProposalError(f"replacement target not found in original: {old!r}")
        if original.find(old, start + 1) != -1:
            raise ProposalError(f"replacement target occurs more than once in original: {old!r}")
        spans.append((start, start + len(old), new))

    spans.sort(key=lambda s: (s[0], s[1]))
    pieces = []
    last_end = 0
    for start, end, new in spans:
        if start < last_end:
            raise ProposalError("replacement spans overlap")
        pieces.append(original[last_end:start])
        pieces.append(new)
        last_end = end
    pieces.append(original[last_end:])
    return "".join(pieces)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_fingerprint(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns, value.st_nlink,
    )


def bounded_file_bytes(root: Path, path: Path, limit: int, label: str) -> bytes:
    """Read one project file without following links or allocating past limit + 1."""
    if limit < 0:
        raise ProposalError("file byte limit cannot be negative")
    root = root.resolve(strict=True)
    candidate = path if path.is_absolute() else root / path
    candidate = Path(os.path.abspath(candidate))
    if not candidate.is_relative_to(root):
        raise ProposalError(f"{label} is outside the project root")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProposalError(f"cannot safely resolve {label}") from exc
    if resolved != candidate:
        raise ProposalError(f"{label} is a symlink or path escape")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise ProposalError(f"cannot safely open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ProposalError(f"{label} is not a regular file")
        # A hardlink can make bytes outside the project reachable through an
        # apparently safe in-project pathname. Proposal inputs must be unique.
        if before.st_nlink != 1:
            raise ProposalError(f"{label} is hardlinked and cannot be trusted")
        if before.st_size > limit:
            raise ProposalError(f"{label} exceeds the safe context budget")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ProposalError(f"{label} exceeds the safe context budget")
        after = os.fstat(descriptor)
        try:
            named = os.stat(candidate, follow_symlinks=False)
            final_resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ProposalError(f"{label} changed while it was being read") from exc
        if (file_fingerprint(before) != file_fingerprint(after)
                or file_fingerprint(after) != file_fingerprint(named)
                or final_resolved != candidate):
            raise ProposalError(f"{label} changed while it was being read")
        return data
    finally:
        os.close(descriptor)


def bounded_file_text(root: Path, path: Path, limit: int, label: str) -> str:
    return bounded_file_bytes(root, path, limit, label).decode("utf-8")


def acquire_proposal_lock(root: Path) -> tuple[int, Path]:
    """Acquire the one nonblocking proposal-generation lock for this worktree."""
    local = root / ".local/two-site-refactor/proposals"
    if local.resolve() != local:
        raise ProposalError("run storage cannot be a symlink")
    local.mkdir(parents=True, exist_ok=True)
    if local.resolve(strict=True) != local:
        raise ProposalError("run storage cannot be a symlink")
    lock_path = local / ".runner.lock"
    flags = (os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ProposalError("cannot safely open proposal runner lock") from exc
    try:
        lock_stat = os.fstat(descriptor)
        if (not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1
                or lock_stat.st_uid != os.geteuid()):
            raise ProposalError("proposal runner lock is not a trusted regular file")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProposalError("another proposal runner is already active") from exc
        return descriptor, local
    except BaseException:
        os.close(descriptor)
        raise


def release_proposal_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def capture_bounded_process(
    process: subprocess.Popen,
    input_bytes: bytes,
    stdout_path: Path,
    stderr_path: Path,
    timeout: float,
    max_output: int = MAX_OUTPUT,
) -> None:
    """Drain all pipes concurrently while enforcing one hard stdout+stderr cap."""
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


def cli_identity(cli: Path) -> dict[str, str]:
    cli = cli.resolve(strict=True)
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ProposalError("CLI must be an executable regular file")
    digest = file_sha256(cli)
    if digest != EXPECTED_CLI_SHA256:
        raise ProposalError("CLI binary hash is not the reviewed pin")
    with tempfile.TemporaryDirectory(prefix="two-site-cli-version-") as temporary:
        temporary_path = Path(temporary)
        with subprocess.Popen(
            [str(cli), "--version"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=dict(os.environ, AGY_CLI_DISABLE_AUTO_UPDATE='true'),
        ) as process:
            capture_bounded_process(
                process,
                b"",
                temporary_path / "stdout",
                temporary_path / "stderr",
                10,
                max_output=4096,
            )
        version_text = bounded_file_text(
            temporary_path, temporary_path / "stdout", 4096, "CLI version output"
        ).strip()
        stderr_text = bounded_file_text(
            temporary_path, temporary_path / "stderr", 4096, "CLI version error output"
        ).strip()
        if process.returncode or stderr_text or version_text != EXPECTED_CLI_VERSION:
            raise ProposalError("CLI version is not the reviewed pin")
    if file_sha256(cli) != digest:
        raise ProposalError("CLI identity changed during version inspection")
    return {"path": str(cli), "version": version_text, "sha256": digest}


def unique_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProposalError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs)


def load_runtime_contract(root: Path) -> dict[str, str]:
    """Load and validate the one machine-readable Gemini execution contract."""
    root = root.resolve(strict=True)
    path = root / GEMINI_RUNTIME_CONTRACT_REL
    contract = unique_json(bounded_file_text(
        root, path, 16 * 1024, "Gemini runtime contract"
    ))
    if not isinstance(contract, dict) or set(contract) != RUNTIME_CONTRACT_KEYS:
        raise ProposalError("Gemini runtime contract has unexpected keys")
    if contract.get("schema_version") != "1.2" or contract.get("provider") != "antigravity":
        raise ProposalError("Gemini runtime contract has an unsupported schema or provider")
    model = contract.get("model")
    effort = contract.get("effort")
    worker_model = contract.get("worker_model")
    worker_effort = contract.get("worker_effort")
    cli_version = contract.get("cli_version")
    cli_sha256 = contract.get("cli_sha256")
    if (not isinstance(model, str) or not re.fullmatch(r"gemini-[A-Za-z0-9][A-Za-z0-9._-]{1,79}", model)
            or not isinstance(effort, str) or effort not in VALID_EFFORTS
            or not isinstance(worker_model, str)
            or not re.fullmatch(r"gemini-[A-Za-z0-9][A-Za-z0-9._-]{1,79}", worker_model)
            or not isinstance(worker_effort, str) or worker_effort not in VALID_EFFORTS
            or not isinstance(cli_version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", cli_version)
            or not isinstance(cli_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", cli_sha256)):
        raise ProposalError("Gemini runtime contract contains an invalid pin")
    return contract


def validate_runtime_contract(root: Path) -> dict[str, str]:
    """Fail before provider invocation when code, docs, or contract drift apart."""
    contract = load_runtime_contract(root)
    loaded_at_startup = globals().get("RUNTIME_CONTRACT")
    if loaded_at_startup is not None and contract != loaded_at_startup:
        raise ProposalError("Gemini runtime contract changed after runner startup; restart is required")
    references = tuple((path, "pair" if path.name == "PROPOSAL_AUTOMATION.md" else
                        "model" if path.name == "TYPED_GATEWAY.md" else "cli")
                       for path in GEMINI_RUNTIME_REFERENCE_PATHS)
    model = contract["model"]
    effort = contract["effort"]
    worker_model = contract["worker_model"]
    worker_effort = contract["worker_effort"]
    for relative, reference_kind in references:
        text = bounded_file_text(root, root / relative, MAX_CONTEXT, f"Gemini runtime reference {relative}")
        if reference_kind == "pair":
            if (f"Worker=`{worker_model}/{worker_effort}`" not in text
                    or f"Reviewer=`{model}/{effort}`" not in text):
                raise ProposalError(f"Gemini runtime reference drift: {relative}")
        else:
            marker = model
            if marker not in text:
                raise ProposalError(f"Gemini runtime reference drift: {relative}")
            if reference_kind == "cli" and f"--model {model} --effort {effort}" not in text:
                raise ProposalError(f"Gemini model/effort reference drift: {relative}")
        for alternate_model in {"gemini-3.8-flash-high", "gemini-3.8-flash-medium"} - {model, worker_model}:
            if alternate_model in text:
                raise ProposalError(f"stale Gemini model reference: {relative}")
        for alternate_effort in VALID_EFFORTS - {effort}:
            if f"--model {model} --effort {alternate_effort}" in text:
                raise ProposalError(f"stale Gemini effort reference: {relative}")
    automation_contract = bounded_file_text(
        root,
        root / Path("docs/refactor/two-server/PROPOSAL_AUTOMATION.md"),
        MAX_CONTEXT,
        "Gemini role effort reference",
    )
    if (f"Worker=`{worker_model}/{worker_effort}`" not in automation_contract
            or f"Reviewer=`{model}/{effort}`" not in automation_contract):
        raise ProposalError("Gemini role effort reference drift")
    return contract


# These values are derived from the tracked contract; no executable file may pin
# a second model/effort pair. validate_runtime_contract() is repeated per run so
# an in-process process cannot continue after the contract changes on disk.
RUNTIME_CONTRACT = load_runtime_contract(ROOT)
MODEL = RUNTIME_CONTRACT["model"]
EFFORT = RUNTIME_CONTRACT["effort"]
WORKER_MODEL = RUNTIME_CONTRACT["worker_model"]
WORKER_EFFORT = RUNTIME_CONTRACT["worker_effort"]
EXPECTED_CLI_VERSION = RUNTIME_CONTRACT["cli_version"]
EXPECTED_CLI_SHA256 = RUNTIME_CONTRACT["cli_sha256"]
ALLOWED_SHELL_SCRIPTS = frozenset({
    "scripts/two_site_autopilot_tmux.sh",
    "scripts/two_site_autopilot_monitor.sh",
})
TRUSTED_SHELL_SCRIPTS = ALLOWED_SHELL_SCRIPTS


def is_retryable_zero_token_stream(raw: str, expected_model: str = MODEL) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        events = [unique_json(line) for line in raw.splitlines() if line.strip()]
    except Exception:
        return False
    if not events or any(not isinstance(e, dict) for e in events):
        return False
    for event in events:
        kind = event.get("event")
        if kind in {"init", "result", "step_update"} and not isinstance(event.get(kind), dict):
            return False
    init = [e for e in events if e.get("event") == "init"]
    terminal = [e.get("result") for e in events if e.get("event") == "result"]
    if len(init) != 1 or len(terminal) != 1:
        return False
    init_payload = init[0].get("init")
    if not isinstance(init_payload, dict) or init_payload.get("model") != expected_model:
        return False
    session = init[0].get("conversation_id")
    if not isinstance(session, str) or not session.strip():
        return False
    envelope = terminal[0]
    if not isinstance(envelope, dict):
        return False
    if envelope.get("conversation_id") != session:
        return False
    for event in events:
        if "conversation_id" in event and event.get("conversation_id") != session:
            return False
        if (
            "tool_name" in event
            or "tool_info" in event
            or "subagent_info" in event
            or event.get("step_type") in {"tool", "subagent"}
        ):
            return False
        if event.get("event") == "step_update":
            step = event.get("step_update")
            if isinstance(step, dict) and (
                "tool_name" in step
                or "tool_info" in step
                or "subagent_info" in step
                or step.get("step_type") in {"tool", "subagent"}
            ):
                return False
    if envelope.get("status") != "ERROR":
        return False
    error = envelope.get("error")
    if error != "Agent execution terminated due to error.":
        return False
    if re.search(
        r"auth|quota|rate[\s_-]*limit|time[\s_-]*out|timed[\s_-]*out|deadline|network|connect",
        error,
        re.IGNORECASE,
    ):
        return False
    response = envelope.get("response")
    if response not in ("", None):
        return False
    if envelope.get("structured_output"):
        return False
    denied = envelope.get("denied_actions")
    if denied not in (None, [], ()):
        return False
    usage = envelope.get("usage")
    if not isinstance(usage, dict) or not usage:
        return False
    if not all(type(v) is int and v == 0 for v in usage.values()):
        return False
    return True


def relative_file(root: Path, name: str) -> Path:
    if (not isinstance(name, str) or not name or "\\" in name or "\0" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ProposalError("noncanonical file path")
    path = PurePosixPath(name)
    if (path.is_absolute() or any(part in PROTECTED for part in path.parts)
            or any(part.startswith(".env") and not part.endswith(".example") for part in path.parts)
            or (path.suffix.lower() not in {".py", ".md", ".json", ".yaml", ".yml", ".ts", ".vue", ".css", ".example"}
                and name not in ALLOWED_SHELL_SCRIPTS)):
        raise ProposalError("file outside source/document scope")
    target = root / name
    if target.resolve() != target or not target.is_relative_to(root):
        raise ProposalError("symlink or path escape")
    return target


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["/usr/bin/git", "--no-replace-objects", "-C", str(root), *args],
        text=True, timeout=20, stderr=subprocess.DEVNULL,
    ).strip()


def canonical_cli_source(root: Path) -> Path:
    common_dir = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common_dir.parent / ".local/tools" / f"agy-{EXPECTED_CLI_VERSION}-{EXPECTED_CLI_SHA256[:16]}.pin"


@contextlib.contextmanager
def proposal_cli_context(
    root: Path,
    cli: Path | None = None,
) -> Iterator[tuple[Path, Path, dict[str, str]]]:
    if cli is None:
        source = canonical_cli_source(root)
        is_default = True
    else:
        source = Path(cli)
        if not source.is_absolute():
            source = Path(os.path.abspath(Path(root) / source))
        is_default = False

    if not os.path.lexists(source):
        raise FileNotFoundError(f"CLI source not found: {source}")

    lock_descriptor, local = acquire_proposal_lock(root)
    try:
        with prepare_disposable_cli(
            source,
            expected_sha256=EXPECTED_CLI_SHA256 if is_default else None,
            require_read_only=is_default,
        ) as (disposable_path, evidence):
            yield local, disposable_path, evidence
    finally:
        release_proposal_lock(lock_descriptor)


def validate_assignment_scope(root: Path, task: dict) -> None:
    scope_vars = (
        "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
        "TWO_SITE_ASSIGNMENT_ID",
        "TWO_SITE_ASSIGNMENT_SHA256",
    )
    present = [var for var in scope_vars if var in os.environ]
    if not present:
        return
    if len(present) != len(scope_vars):
        raise ProposalError("assignment scope validation failed")
    required = os.environ["TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED"]
    assignment_id = os.environ["TWO_SITE_ASSIGNMENT_ID"]
    assignment_sha256 = os.environ["TWO_SITE_ASSIGNMENT_SHA256"]
    if required != "1" or not assignment_id.strip() or not assignment_sha256.strip():
        raise ProposalError("assignment scope validation failed")
    try:
        scope = load_expected_assignment_scope(root, assignment_id, assignment_sha256)
        validate_task_scope(scope, task)
    except AssignmentScopeError as exc:
        raise ProposalError("assignment scope validation failed") from exc
    except Exception as exc:
        raise ProposalError("assignment scope validation failed") from exc


def load_task(root: Path, task_path: Path) -> tuple[dict, dict]:
    raw = bounded_file_bytes(root, task_path, MAX_CONTEXT, "task")
    task = unique_json(raw.decode())
    required = {"task_id", "goal", "context_files", "allowed_files", "requirements", "test_selectors"}
    if not isinstance(task, dict) or set(task) != required:
        raise ProposalError("task keys must match the documented contract")
    if (not isinstance(task["task_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", task["task_id"])):
        raise ProposalError("invalid task ID")
    if not isinstance(task["goal"], str) or not task["goal"].strip():
        raise ProposalError("task goal is missing")
    for key in ("context_files", "allowed_files", "requirements", "test_selectors"):
        values = task[key]
        if (not isinstance(values, list) or not values or len(values) > 32
                or any(not isinstance(x, str) or not x.strip() for x in values)
                or len(set(values)) != len(values)):
            raise ProposalError("task lists must contain unique bounded nonempty strings")
    if SECRET.search(json.dumps(task, ensure_ascii=False)):
        raise ProposalError("credential-like content excluded from model input")
    validate_assignment_scope(root, task)
    if "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n" in task["goal"]:
        try:
            from scripts.two_site_cycle_tools import validate_excerpts
        except ModuleNotFoundError:
            from two_site_cycle_tools import validate_excerpts
        validate_excerpts(root, task)
    sources = {}
    for name in dict.fromkeys(task["context_files"] + task["allowed_files"]):
        target = relative_file(root, name)
        if not target.exists():
            if name in task["context_files"]:
                raise ProposalError("required context file is missing: " + name)
            sources[name] = {"sha256": None, "content": None}
            continue
        content = bounded_file_bytes(root, target, MAX_CONTEXT, "context file " + name)
        if SECRET.search(content.decode()):
            raise ProposalError("credential-like content excluded from model input")
        sources[name] = {"sha256": sha(content), "content": content.decode()}
    if len(json.dumps({"task": task, "sources": sources}).encode()) > MAX_CONTEXT:
        raise ProposalError("split the task: supplied context exceeds budget")
    return task, sources


def validate_task_readiness(
    task_or_notes: dict,
    sources: dict | None = None,
    notes: dict | None = None,
) -> dict:
    """Deterministic readiness validator for dependency context."""
    if isinstance(sources, dict):
        if notes is None:
            notes = task_preparation_notes(task_or_notes, sources)
    else:
        notes = task_or_notes
    dep_ctx = notes.get("dependency_context") if isinstance(notes, dict) else {}
    if isinstance(dep_ctx, dict) and dep_ctx.get("persistence_contract_requested"):
        missing = [p for p in dep_ctx.get("missing_full_model_paths", []) if isinstance(p, str)]
        if missing:
            bounded_missing = sorted(dict.fromkeys(missing))[:16]
            raise ProposalError(f"missing model context: {', '.join(bounded_missing)}")
    return notes


def check(root: Path, task_path: Path) -> dict:
    root = root.resolve(strict=True)
    task, sources = load_task(root, task_path)
    notes = task_preparation_notes(task, sources)
    validate_task_readiness(notes)
    snapshot = json.dumps({"task": task, "sources": sources}).encode("utf-8")
    return {
        "state": "TASK_READY",
        "task_id": task["task_id"],
        "source_file_count": len(sources),
        "byte_count": len(snapshot),
        "preparation_notes": notes,
    }


def task_preparation_notes(task: dict, sources: dict) -> dict:
    """Advisory only; derived from the immutable task, never a proof identity."""
    try:
        from scripts.two_site_task_context import preparation_notes
    except ModuleNotFoundError:
        from two_site_task_context import preparation_notes
    return preparation_notes(task, sources)


def originally_new_paths(task: dict, sources: dict) -> tuple[str, ...]:
    """Return original absent paths in assignment order, before candidate overlay."""
    return tuple(
        path for path in task["allowed_files"]
        if sources[path]["sha256"] is None and sources[path]["content"] is None
    )


def schema(
    role: str,
    task: dict | None = None,
    sources: dict | None = None,
    force_full_paths: tuple[str, ...] = (),
) -> dict:
    if (task is None) != (sources is None):
        raise ProposalError("task and sources must both be provided or both omitted")
    fields = {
        "status": {"type": "string", "enum": ["PROPOSAL_READY", "BLOCKED"] if role == "worker" else ["VERIFIED", "CHANGES_REQUIRED", "BLOCKED"]},
        "summary": {"type": "string"},
        "covered_requirements": {"type": "array", "items": {"type": "integer"}},
        "findings": {"type": "array", "items": {"type": "string"}},
        "changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["path", "original_sha256", "content", "replacements"],
            "properties": {"path": {"type": "string"},
                           "original_sha256": {"type": ["string", "null"]},
                           "content": {"type": ["string", "null"]},
                           "replacements": {
                               "type": "array",
                               "items": {
                                   "type": "object",
                                   "additionalProperties": False,
                                   "required": ["old", "new"],
                                   "properties": {
                                       "old": {"type": "string"},
                                       "new": {"type": "string"},
                                   },
                               },
                           }},
        }},
    }
    if task is not None and sources is not None:
        fields["changes"] = build_changes_schema(
            role, task["allowed_files"], sources, MAX_REPLACEMENTS,
            force_full_paths=force_full_paths,
        )
        if role == "worker":
            notes = task_preparation_notes(task, sources)
            out_contract = notes.get("output_contract", {}) if isinstance(notes, dict) else {}
            if out_contract.get("test_only_task"):
                new_tests = set(out_contract.get("new_test_files") or [
                    p for p in task["allowed_files"]
                    if sources.get(p, {}).get("content") is None and sources.get(p, {}).get("sha256") is None
                ])
                max_new_test = out_contract.get("max_new_test_file_utf8_bytes", 24000)
                _apply_new_test_max_length(fields["changes"], new_tests, max_new_test)
    return {"type": "object", "additionalProperties": False,
            "required": list(fields), "properties": fields}


def _apply_new_test_max_length(node: object, new_tests: set[str], max_len: int) -> None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict) and "content" in props and "path" in props:
            path_schema = props.get("path")
            matched = False
            if isinstance(path_schema, dict):
                const_path = path_schema.get("const")
                enum_paths = path_schema.get("enum")
                if const_path in new_tests:
                    matched = True
                elif isinstance(enum_paths, list) and any(p in new_tests for p in enum_paths):
                    matched = True
                elif path_schema.get("type") == "string":
                    matched = True
            if matched and isinstance(props["content"], dict):
                props["content"]["maxLength"] = max_len
        for val in node.values():
            _apply_new_test_max_length(val, new_tests, max_len)
    elif isinstance(node, list):
        for item in node:
            _apply_new_test_max_length(item, new_tests, max_len)


def normalize_result(
    result: dict,
    task: dict,
    sources: dict,
    role: str,
    strict_transport: bool = False,
    force_full_paths: tuple[str, ...] = (),
) -> dict:
    if type(strict_transport) is not bool:
        raise ProposalError("strict_transport must be a boolean")
    if (not isinstance(force_full_paths, tuple)
            or any(not isinstance(path, str) or not path for path in force_full_paths)
            or len(set(force_full_paths)) != len(force_full_paths)
            or not set(force_full_paths).issubset(task["allowed_files"])):
        raise ProposalError("force_full_paths must be an ordered unique tuple within allowed files")
    forced = set(force_full_paths)
    if not isinstance(result, dict):
        raise ProposalError("incomplete response contract")
    if role == "verifier" or result.get("status") != "PROPOSAL_READY":
        return dict(result)

    changes = result.get("changes")
    if not isinstance(changes, list):
        raise ProposalError("malformed response list")
    if not changes:
        raise ProposalError("plan-only or no-op response is not implementation")

    seen = set()
    normalized_changes = []

    for change in changes:
        if not isinstance(change, dict):
            raise ProposalError("invalid change shape")
        keys = set(change.keys())
        if keys == {"path", "original_sha256", "content"}:
            change_mode = "legacy"
            if not isinstance(change["content"], str):
                raise ProposalError("invalid change shape")
        elif keys == {"path", "original_sha256", "content", "replacements"}:
            content = change["content"]
            replacements = change["replacements"]
            if not (isinstance(content, str) or content is None) or not isinstance(replacements, list):
                raise ProposalError("invalid change shape")
            if isinstance(content, str) and len(replacements) == 0:
                change_mode = "full"
            elif content is None and len(replacements) > 0:
                change_mode = "replacement"
            elif isinstance(content, str) and len(replacements) > 0:
                raise ProposalError("mixed change content and replacements")
            else:
                raise ProposalError("empty change content and replacements")
        else:
            raise ProposalError("invalid change shape")

        name = change["path"]
        if (not isinstance(name, str) or name not in task["allowed_files"]
                or name not in sources or name in seen):
            raise ProposalError("changed path is duplicated or outside assignment")
        seen.add(name)

        if change["original_sha256"] != sources[name]["sha256"]:
            raise ProposalError("proposal is based on different source bytes")

        if strict_transport and (change_mode != "legacy" or name in forced):
            try:
                validate_change_mode(
                    change, sources[name], MAX_REPLACEMENTS,
                    force_full=name in forced,
                )
            except TransportPolicyError as exc:
                raise ProposalError("proposal transport policy violation") from exc

        if change_mode == "replacement":
            if sources[name]["content"] is None:
                raise ProposalError("new-file replacement is not supported")
            materialized = materialize_replacements(sources[name]["content"], change["replacements"])
            normalized_changes.append({
                "path": name,
                "original_sha256": change["original_sha256"],
                "content": materialized,
            })
        else:
            normalized_changes.append({
                "path": name,
                "original_sha256": change["original_sha256"],
                "content": change["content"],
            })

    normalized_result = dict(result)
    normalized_result["changes"] = normalized_changes
    return normalized_result


def validate_result(result: dict, task: dict, sources: dict, role: str) -> dict:
    if not isinstance(result, dict) or set(result) != set(schema(role)["required"]):
        raise ProposalError("incomplete response contract")
    statuses = schema(role)["properties"]["status"]["enum"]
    if result["status"] not in statuses or not isinstance(result["summary"], str) or not result["summary"].strip():
        raise ProposalError("missing status or explanation")
    for field in ("findings", "covered_requirements", "changes"):
        if not isinstance(result[field], list):
            raise ProposalError("malformed response list")
    if any(not isinstance(x, str) or not x.strip() for x in result["findings"]):
        raise ProposalError("findings must be explicit strings")
    coverage = result["covered_requirements"]
    if any(type(x) is not int for x in coverage) or len(set(coverage)) != len(coverage):
        raise ProposalError("invalid requirement coverage")
    success = result["status"] in {"PROPOSAL_READY", "VERIFIED"}
    if success and (set(coverage) != set(range(1, len(task["requirements"]) + 1)) or result["findings"]):
        raise ProposalError("success omits requirements or has unresolved findings")
    if not success and (not result["findings"] or result["changes"]):
        raise ProposalError("blocked response needs findings and no partial changes")
    if role == "verifier" and result["changes"]:
        raise ProposalError("verifier cannot author changes")
    if role == "worker" and success and not result["changes"]:
        raise ProposalError("plan-only or no-op response is not implementation")
    notes = task_preparation_notes(task, sources)
    out_contract = notes.get("output_contract", {}) if isinstance(notes, dict) else {}
    test_only = bool(out_contract.get("test_only_task"))
    max_transport = out_contract.get("max_test_only_transport_bytes", 64000)
    max_new_test = out_contract.get("max_new_test_file_utf8_bytes", 24000)
    new_test_files = set(out_contract.get("new_test_files", []))

    if role == "worker" and test_only:
        raw_transport_bytes = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        if raw_transport_bytes > max_transport:
            raise ProposalError("test-only raw transport exceeds budget")

    seen = set()
    for change in result["changes"]:
        if not isinstance(change, dict) or set(change) != {"path", "original_sha256", "content"}:
            raise ProposalError("invalid change shape")
        name = change["path"]
        if not isinstance(name, str) or name not in task["allowed_files"] or name in seen:
            raise ProposalError("changed path is duplicated or outside assignment")
        seen.add(name)
        if change["original_sha256"] != sources[name]["sha256"]:
            raise ProposalError("proposal is based on different source bytes")
        if (not isinstance(change["content"], str) or not change["content"].strip()
                or change["content"] == sources[name]["content"]):
            raise ProposalError("empty or unchanged file")
        if SECRET.search(change["content"]) or "\0" in change["content"]:
            raise ProposalError("unsafe generated content")
        if role == "worker" and test_only:
            is_new = sources[name]["sha256"] is None and sources[name]["content"] is None
            if (is_new or name in new_test_files) and len(change["content"].encode("utf-8")) > max_new_test:
                raise ProposalError(f"new test file content exceeds budget: {name}")
    return result


def load_prior_worker(
    root: Path,
    proposal_path: Path,
    proposal_text: str,
    task: dict,
    sources: dict,
    head: str,
) -> tuple[dict, dict, str]:
    """Bind a review/correction to one complete worker run and exact proposal bytes."""
    baseline = unique_json(bounded_file_text(
        root, proposal_path.with_name("task.json"), 2 * MAX_CONTEXT, "worker snapshot"
    ))
    worker_status = unique_json(bounded_file_text(
        root, proposal_path.with_name("status.json"), MAX_CONTEXT, "worker status"
    ))
    proposal_sha256 = sha(proposal_text.encode("utf-8"))
    if (not isinstance(baseline, dict) or baseline.get("task") != task
            or baseline.get("source_head") != head or baseline.get("sources") != sources):
        raise ProposalError("verify before applying: worker baseline differs from current source/task")
    if (not isinstance(worker_status, dict)
            or worker_status.get("role") != "worker"
            or worker_status.get("state") != "PROPOSAL_READY"
            or worker_status.get("result_sha256") != proposal_sha256
            or not isinstance(worker_status.get("run_id"), str)
            or not isinstance(worker_status.get("conversation_id"), str)
            or not worker_status["conversation_id"].strip()):
        raise ProposalError("proposal is not bound to one completed worker run")
    candidate = validate_result(unique_json(proposal_text), task, sources, "worker")
    if candidate["status"] != "PROPOSAL_READY":
        raise ProposalError("review/correction requires a complete worker proposal")
    return candidate, worker_status, proposal_sha256


def validate_correction_feedback(text: str, proposal_sha256: str) -> dict:
    feedback = unique_json(text)
    required = {"reviewer", "reviewed_proposal_sha256", "findings", "test_evidence"}
    if not isinstance(feedback, dict) or set(feedback) != required:
        raise ProposalError("correction feedback does not match the exact contract")
    if feedback["reviewer"] not in {"gemini", "codex", "test-gate"}:
        raise ProposalError("correction feedback reviewer is invalid")
    if feedback["reviewed_proposal_sha256"] != proposal_sha256:
        raise ProposalError("correction feedback targets a different proposal")
    for field, allow_empty in (("findings", False), ("test_evidence", True)):
        values = feedback[field]
        if (not isinstance(values, list) or len(values) > 32
                or (not allow_empty and not values)
                or any(not isinstance(value, str) or not value.strip() for value in values)):
            raise ProposalError(f"correction feedback {field} is invalid")
    return feedback


def parse_stream(raw: str, task: dict, sources: dict, role: str) -> tuple[dict, dict]:
    return _parse_stream(raw, task, sources, role)


def _parse_stream(
    raw: str,
    task: dict,
    sources: dict,
    role: str,
    force_full_paths: tuple[str, ...] = (),
    expected_model: str = MODEL,
) -> tuple[dict, dict]:
    events = [unique_json(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(e, dict) for e in events):
        raise ProposalError("CLI event must be an object")
    for event in events:
        kind = event.get("event")
        if kind in {"init", "result", "step_update"} and not isinstance(event.get(kind), dict):
            raise ProposalError("malformed CLI event envelope")
    init = [e for e in events if e.get("event") == "init"]
    terminal = [e["result"] for e in events if e.get("event") == "result"]
    if len(init) != 1 or init[0]["init"].get("model") != expected_model or len(terminal) != 1:
        raise ProposalError("missing unique model/session/result evidence")
    envelope = terminal[0]
    session = init[0].get("conversation_id")
    if (not isinstance(session, str) or not session.strip()
            or envelope.get("conversation_id") != session):
        raise ProposalError("CLI did not complete one successful session")
    if envelope.get("denied_actions"):
        raise RetryableProviderSessionError("CLI did not complete one successful session")
    if envelope.get("status") != "SUCCESS":
        error = envelope.get("error")
        if (isinstance(error, str)
                and not re.search(
                    r"auth|quota|rate[\s_-]*limit|time[\s_-]*out|timed[\s_-]*out|deadline|network|connect",
                    error, re.IGNORECASE,
                )
                and re.search(
                    r"MODEL_OUTPUT_TRUNCATED|"
                    r"exceed.*output[-\s]+tokens?[-\s]+limit|"
                    r"output[-\s]+tokens?[-\s]+limit.*exceed",
                    error, re.IGNORECASE | re.DOTALL,
                )):
            raise ProposalError("MODEL_OUTPUT_TRUNCATED: rescope to a smaller task")
        if (isinstance(error, str)
                and not re.search(
                    r"auth|quota|rate[\s_-]*limit|time[\s_-]*out|timed[\s_-]*out|deadline|network|connect",
                    error, re.IGNORECASE,
                )
                and re.search(r"improperly formatted function call", error, re.IGNORECASE)):
            raise RetryableProviderOutputError(
                "provider returned an invalid response envelope"
            )
        raise ProposalError("CLI did not complete one successful session")

    finish_submissions = 0
    for event in events:
        if "conversation_id" in event and event.get("conversation_id") != session:
            raise ProposalError("CLI did not complete one successful session")
        if (
            "tool_name" in event
            or "tool_info" in event
            or "subagent_info" in event
            or event.get("step_type") in {"tool", "subagent"}
        ):
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
        if event.get("event") != "step_update":
            continue
        step = event["step_update"]
        step_cid = step.get("conversation_id")
        if "conversation_id" in step and step_cid != session:
            raise ProposalError("CLI did not complete one successful session")
        is_tool_or_subagent = (
            "tool_name" in step
            or "tool_info" in step
            or "subagent_info" in step
            or step.get("step_type") in {"tool", "subagent"}
        )
        if not is_tool_or_subagent:
            continue
        tool_info = step.get("tool_info")
        params = tool_info.get("parameters") if isinstance(tool_info, dict) else None
        is_finish_submission = (
            step.get("step_type") == "tool"
            and step.get("tool_name") == "finish"
            and "subagent_info" not in step
            and isinstance(tool_info, dict)
            and set(tool_info.keys()) == {"name", "parameters"}
            and tool_info.get("name") == "finish"
            and isinstance(params, dict)
            and set(params.keys()) == CANONICAL_RESPONSE_KEYS
            and (step_cid is None or step_cid == session)
            and (event.get("conversation_id") is None or event.get("conversation_id") == session)
        )
        if not is_finish_submission or finish_submissions >= 1:
            raise RetryableProviderSessionError("tool attempt invalidates a proposal-only run")
        finish_submissions += 1

    # With --json-schema the documented machine payload is structured_output.
    # CLI 1.1.27 may also append its submission object to free-text response.
    # Never scrape that text or fall back when a supplied structured value is bad.
    if "structured_output" in envelope:
        result = envelope["structured_output"]
    else:
        if not isinstance(envelope.get("response"), str):
            raise RetryableProviderSessionError("CLI result has no response text")
        try:
            result = unique_json(envelope["response"])
        except json.JSONDecodeError as exc:
            raise RetryableProviderSessionError(
                "CLI response is not one valid JSON document"
            ) from exc
    try:
        result = normalize_result(
            result, task, sources, role, strict_transport=True,
            force_full_paths=force_full_paths,
        )
        result = validate_result(result, task, sources, role)
    except ProposalError as exc:
        if role == "worker" and _provider_output_is_retryable(exc):
            raise RetryableProviderOutputError(
                "provider returned unusable proposal transport"
            ) from exc
        raise
    if len(serialize_json(result)) > MAX_CONTEXT:
        raise ProposalError("canonical proposal exceeds the safe context budget")
    return result, envelope


def proposal_diff(changes: list, sources: dict) -> str:
    patch = ""
    for change in changes:
        old = sources[change["path"]]["content"]
        for line in difflib.unified_diff(
            (old or "").splitlines(keepends=True), change["content"].splitlines(keepends=True),
            fromfile="a/" + change["path"] if old is not None else "/dev/null",
            tofile="b/" + change["path"],
        ):
            patch += line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
    return patch


def serialize_json(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build_review_transport(
    candidate: dict,
    sources: dict,
    proposal_sha256: str,
) -> dict:
    if not isinstance(candidate, dict) or not isinstance(sources, dict):
        raise ProposalError("invalid review transport input")
    if not isinstance(proposal_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", proposal_sha256):
        raise ProposalError("invalid proposal SHA")
    changes = candidate.get("changes")
    if not isinstance(changes, list):
        raise ProposalError("invalid candidate changes")
    return {
        "canonical_proposal_sha256": proposal_sha256,
        "status": candidate["status"],
        "summary": candidate["summary"],
        "covered_requirements": list(candidate["covered_requirements"]),
        "findings": list(candidate["findings"]),
        "changed_paths": [change["path"] for change in changes],
        "unified_diff": proposal_diff(changes, sources),
    }


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(serialize_json(value))
    temporary.replace(path)


def run(root: Path, task_path: Path, role: str, run_id: str, timeout: int,
        feedback: Path | None = None, proposal: Path | None = None,
        cli: Path | None = None, test_evidence: Path | None = None) -> Path:
    root = root.resolve(strict=True)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", run_id) or not 10 <= timeout <= 900:
        raise ProposalError("invalid run ID or timeout")
    if role not in {"worker", "verifier"}:
        raise ProposalError("invalid proposal role")
    autopilot_run_id = validate_autopilot_run_id(os.environ.get(AUTOPILOT_RUN_ID_ENV))
    # Contract/document drift must be rejected before task materialization and
    # before the Gemini CLI can consume a token or create a run artifact.
    validate_runtime_contract(root)
    task, sources = load_task(root, task_path)
    prep_notes = task_preparation_notes(task, sources)
    validate_task_readiness(prep_notes)
    head = git(root, "rev-parse", "HEAD")
    initial_status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    material = {"task": task, "source_head": head, "sources": sources}
    input_hashes = {}
    for key, path in (("feedback", feedback), ("proposal", proposal), ("test_evidence", test_evidence)):
        if path is not None:
            content = bounded_file_text(root, path, MAX_CONTEXT, key)
            if SECRET.search(content):
                raise ProposalError("credential-like content excluded from model input")
            material[key] = content
            input_hashes[key] = sha(content.encode("utf-8"))
    if role == "verifier" and proposal is None:
        raise ProposalError("verifier needs the exact proposal")
    if role == "verifier" and feedback is not None:
        raise ProposalError("first-pass Gemini verifier does not accept feedback")
    if role == "worker" and ((feedback is None) != (proposal is None)):
        raise ProposalError("correction worker requires both proposal and feedback")
    if test_evidence is not None and (role != "verifier" or proposal is None):
        raise ProposalError("test evidence is only allowed for verifier with a proposal")
    prior_status = None
    prompt_material = material
    review_transport_sha = None
    candidate_sources = None
    candidate_sources_sha256 = None
    force_full_paths: tuple[str, ...] = ()
    if proposal is not None:
        candidate, prior_status, proposal_sha256 = load_prior_worker(
            root, proposal, material["proposal"], task, sources, head
        )
        if input_hashes["proposal"] != proposal_sha256:
            raise ProposalError("proposal hash changed during validation")
        if role == "worker":
            material["feedback"] = validate_correction_feedback(
                material["feedback"], proposal_sha256
            )
            candidate_sources = build_candidate_sources(
                task["allowed_files"], sources, candidate["changes"]
            )
            candidate_sources_sha256 = sha(serialize_json(candidate_sources))
            force_full_paths = originally_new_paths(task, sources)
            prompt_material = {
                "task": task,
                "source_head": head,
                "sources": candidate_sources,
                "feedback": material["feedback"],
                "prior_identity": {
                    "run_id": prior_status["run_id"],
                    "proposal_sha256": proposal_sha256,
                },
                "correction_transport": {
                    "full_content_required_paths": list(force_full_paths),
                },
            }
        elif role == "verifier":
            review_proposal = build_review_transport(candidate, sources, proposal_sha256)
            review_transport_bytes = serialize_json(review_proposal)
            if len(review_transport_bytes) > MAX_REVIEW_TRANSPORT_BYTES:
                raise ProposalError("review transport exceeds context budget")
            review_transport_sha = sha(review_transport_bytes)
            prompt_material = {
                "task": task,
                "source_head": head,
                "sources": sources,
                "proposal": review_proposal,
            }
            if test_evidence is not None:
                receipt = unique_json(material["test_evidence"])
                normalized_receipt = validate_test_receipt(
                    receipt, proposal_sha256, require_passed=True
                )
                prompt_material["pre_review_test_evidence"] = normalized_receipt
                material["test_evidence"] = normalized_receipt
    prompt_material = dict(prompt_material)
    prompt_material["preparation_notes"] = prep_notes
    prompt = bounded_file_text(
        ROOT, ROOT / "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md",
        MAX_CONTEXT, "prompt template",
    )
    prompt += "\nROLE: " + role + "\nCONTEXT_JSON:\n" + json.dumps(prompt_material, ensure_ascii=False)
    if len(prompt.encode()) > 2 * MAX_CONTEXT:
        raise ProposalError("split task or feedback into a smaller coherent unit")
    if SECRET.search(prompt):
        raise ProposalError("credential-like content excluded from model input")
    with proposal_cli_context(root, cli) as (local, disposable_cli, cli_source):
        # The lock covers every CLI invocation, including identity probes.
        initial_cli_identity = cli_identity(disposable_cli)
        out = local / run_id
        out.mkdir(mode=0o700)  # Evidence is never overwritten by a retry.
        workspace = out / "workspace"
        (workspace / ".agents").mkdir(parents=True)
        write_json(workspace / ".agents/hooks.json", DENY_HOOK)
        write_json(out / "task.json", material)
        (out / "prompt.txt").write_text(prompt)
        role_model = WORKER_MODEL if role == "worker" else MODEL
        role_effort = WORKER_EFFORT if role == "worker" else EFFORT
        meta = {"run_id": run_id, "task_id": task["task_id"], "role": role,
                "head": head, "model": role_model, "effort": role_effort, "state": "RUNNING", "started_at": time.time(),
                "retain_until": time.time() + 7 * 86400,
                "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE",
                "cli": initial_cli_identity, "cli_source": cli_source}
        if autopilot_run_id is not None:
            meta["controller_run_id"] = autopilot_run_id
        if prior_status is not None:
            meta.update(
                input_worker_run_id=prior_status["run_id"],
                input_worker_conversation_id=prior_status["conversation_id"],
                input_proposal_sha256=input_hashes["proposal"],
            )
        if feedback is not None:
            meta["input_feedback_sha256"] = input_hashes["feedback"]
        if test_evidence is not None:
            meta["input_test_evidence_sha256"] = input_hashes["test_evidence"]
        if review_transport_sha is not None:
            meta["review_transport_sha256"] = review_transport_sha
        if candidate_sources_sha256 is not None:
            meta["candidate_sources_sha256"] = candidate_sources_sha256
        write_json(out / "status.json", meta)
        active_sources = candidate_sources if candidate_sources is not None else sources
        command = [str(disposable_cli), "--mode", "plan", "--sandbox", "--model", role_model,
                   "--effort", role_effort, "--input-format", "stream-json",
                   "--output-format", "stream-json", "--print-timeout", f"{timeout}s",
                   "--json-schema", json.dumps(schema(
                       role, task, active_sources, force_full_paths
                   ))]
        try:
            # Auth belongs to the CLI. No credential files are read or copied here.
            with subprocess.Popen(command, cwd=workspace, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  start_new_session=True,
                                  env=dict(os.environ, AGY_CLI_DISABLE_AUTO_UPDATE='true')) as process:
                meta["cli_pid"] = process.pid
                write_json(out / "status.json", meta)
                capture_bounded_process(
                    process,
                    (json.dumps({"event": "user", "message": {"content": prompt}}) + "\n").encode(),
                    out / "stream.jsonl",
                    out / "stderr.txt",
                    timeout + 15,
                )
            if process.returncode:
                raw_stream = bounded_file_text(root, out / "stream.jsonl", MAX_OUTPUT, "CLI event stream")
                if is_retryable_zero_token_stream(raw_stream, role_model):
                    final_cli_identity = cli_identity(disposable_cli)
                    if final_cli_identity != initial_cli_identity:
                        raise ProposalError("CLI identity changed during generation")
                    raise RetryableProviderSessionError("CLI did not complete one successful session")
                raise ProposalError(f"CLI failed with exit {process.returncode}; inspect run evidence")
            final_cli_identity = cli_identity(disposable_cli)
            if final_cli_identity != initial_cli_identity:
                raise ProposalError("CLI identity changed during generation")
            raw = bounded_file_text(root, out / "stream.jsonl", MAX_OUTPUT, "CLI event stream")
            result, envelope = _parse_stream(
                raw, task, active_sources, role, force_full_paths, role_model
            )
            if (prior_status is not None
                    and envelope["conversation_id"] == prior_status["conversation_id"]):
                raise ProposalError("review/correction reused the worker session")
            # A late parent/source change invalidates the entire candidate.
            if head != git(root, "rev-parse", "HEAD") or initial_status != git(root, "status", "--porcelain=v1", "--untracked-files=all"):
                raise ProposalError("Git state changed during generation")
            current_task, current_sources = load_task(root, task_path)
            if current_task != task or current_sources != sources:
                raise ProposalError("task or source bytes changed during generation")
            if candidate_sources is not None:
                if result.get("status") != "PROPOSAL_READY":
                    raise ProposalError("correction delta proposal is not ready")
                delta_result = result
                try:
                    rebased = rebase_correction_result(
                        delta_result, task["allowed_files"], sources, candidate_sources
                    )
                except CorrectionDeltaError as exc:
                    message = str(exc)
                    if (message.startswith("complete no-op rejection:")
                            or " in delta result" in message
                            or message.startswith("delta result ")):
                        raise RetryableProviderOutputError(
                            "provider returned unusable correction delta"
                        ) from exc
                    raise
                rebased = validate_result(rebased, task, sources, role)
                if len(serialize_json(rebased)) > MAX_CONTEXT:
                    raise ProposalError("canonical proposal exceeds the safe context budget")
                write_json(out / "delta-result.json", delta_result)
                meta["delta_result_sha256"] = file_sha256(out / "delta-result.json")
                result = rebased
            write_json(out / "result.json", result)
            (out / "proposal.patch").write_text(proposal_diff(result["changes"], sources))
            meta.update(state=result["status"], conversation_id=envelope["conversation_id"],
                        duration_seconds=round(time.time() - meta["started_at"], 3),
                        usage=envelope.get("usage", {}),
                        result_sha256=file_sha256(out / "result.json"))
        except BaseException as exc:
            meta.update(state="BLOCKED", error=type(exc).__name__ + ": " + str(exc),
                        duration_seconds=round(time.time() - meta["started_at"], 3))
            write_json(out / "status.json", meta)
            raise
        write_json(out / "status.json", meta)
        return out


def run_standalone(
    root: Path,
    task_path: Path,
    role: str,
    base_run_id: str,
    timeout: int,
    feedback: Path | None = None,
    proposal: Path | None = None,
    cli: Path | None = None,
    test_evidence: Path | None = None,
) -> Path:
    validate_autopilot_run_id(os.environ.get(AUTOPILOT_RUN_ID_ENV))
    proposals_dir = root / ".local/two-site-refactor/proposals"
    attempts = (
        base_run_id,
        f"{base_run_id}-retry-1",
        f"{base_run_id}-retry-2",
    )
    last_error: RetryableProviderSessionError | None = None
    label = "reviewer" if role == "verifier" else "worker"
    for attempt_id in attempts:
        if (proposals_dir / attempt_id).exists():
            raise ProposalError(f"{label} run ID already exists: {attempt_id}")
        try:
            return run(
                root,
                task_path,
                role,
                attempt_id,
                timeout,
                feedback=feedback,
                proposal=proposal,
                cli=cli,
                test_evidence=test_evidence,
            )
        except RetryableProviderSessionError as exc:
            last_error = exc
    raise ProposalError(
        f"provider session retry exhausted for {base_run_id} after 3 attempts"
    ) from last_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("worker", "verifier", "check"))
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--feedback", type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--cli", type=Path, help="existing Antigravity executable (controller-owned)")
    parser.add_argument("--test-evidence", type=Path)
    args = parser.parse_args()
    if args.role in ("worker", "verifier") and args.run_id is None:
        parser.error("the following arguments are required: --run-id")
    try:
        if args.role == "check":
            if (args.run_id is not None or args.feedback is not None
                    or args.proposal is not None or args.cli is not None
                    or args.test_evidence is not None):
                raise ProposalError("check mode does not accept run-id, feedback, proposal, cli, or test-evidence arguments")
            payload = check(args.root, args.task)
            print(json.dumps(payload))
            return 0
        out = run_standalone(
            args.root,
            args.task,
            args.role,
            args.run_id,
            args.timeout,
            feedback=args.feedback,
            proposal=args.proposal,
            cli=args.cli,
            test_evidence=args.test_evidence,
        )
        meta = unique_json(bounded_file_text(
            args.root.resolve(strict=True), out / "status.json", MAX_CONTEXT, "run status"
        ))
        print(json.dumps({"state": meta["state"], "evidence": str(out),
                          "seconds": meta["duration_seconds"]}))
        return 0 if meta["state"] in {"PROPOSAL_READY", "VERIFIED"} else 1
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "BLOCKED", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
