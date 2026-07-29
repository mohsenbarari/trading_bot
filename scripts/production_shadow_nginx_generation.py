#!/usr/bin/env python3
"""Build and apply coordinated, reversible production Nginx generations.

The producer reads the three existing production vhosts through stable file
descriptors and emits four deterministic generations for each role.  The host
worker is local-only: it validates and installs one role archive, tests a
complete candidate configuration, and performs bounded file replacement with
durable rollback state.  Cross-host ordering and external readback belong to
the production cutover controller.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import UUID


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import canonical_json_bytes  # noqa: E402
from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from core.three_site_topology import BOT_FI_HOST, WEBAPP_FI_HOST  # noqa: E402


PRODUCER_SCHEMA = "production-shadow-nginx-generation-aggregate-v1"
ROLE_MANIFEST_SCHEMA = "production-shadow-nginx-role-generation-manifest-v1"
JOURNAL_SCHEMA = "production-shadow-nginx-host-journal-v1"
ARCHIVE_FORMAT = "production-shadow-nginx-role-generations-tar-v1"
HOST_ACTION_RESULT_SCHEMA = "production-shadow-nginx-host-action-result-v1"
HOST_FRESH_READBACK_SCHEMA = "production-shadow-nginx-host-fresh-readback-v2"
HOST_READBACK_CHALLENGE_SCHEMA = (
    "production-shadow-nginx-host-readback-challenge-v1"
)
GENERATION_STATES = (
    "legacy-normal",
    "legacy-frozen",
    "shadow-readonly",
    "shadow-writable",
)
ROLES = ("bot_fi", "webapp_fi")
ROLE_HOSTS = {"bot_fi": BOT_FI_HOST, "webapp_fi": WEBAPP_FI_HOST}
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MARKER = "# production-shadow-generation:"
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_COMMAND_STREAM_BYTES = 1024 * 1024
MAX_COMMAND_TIMEOUT_SECONDS = 30
COMMAND_TERM_GRACE_SECONDS = 2.0
PROCESS_POLL_SECONDS = 0.05
PROCESS_TREE_QUIESCENCE_SECONDS = 0.1
PR_SET_CHILD_SUBREAPER = 36
DEFAULT_RELOAD_STABILITY_OBSERVATIONS = 3
DEFAULT_RELOAD_STABILITY_INTERVAL_SECONDS = 1.0
READBACK_CHALLENGE_TTL_SECONDS = 300
READBACK_MAX_CLOCK_SKEW_SECONDS = 5
DEFAULT_OPERATION_BASE = Path("/etc/trading-bot-production-shadow/nginx-generations")
DEFAULT_NGINX = Path("/usr/sbin/nginx")
DEFAULT_NGINX_CONF = Path("/etc/nginx/nginx.conf")
DEFAULT_SITES_AVAILABLE = Path("/etc/nginx/sites-available")
DEFAULT_SITES_ENABLED = Path("/etc/nginx/sites-enabled")
DEFAULT_RELOAD_ARGV = ("/usr/bin/systemctl", "reload", "nginx")
PEM_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN CERTIFICATE-----",
)
FIXED_DESTINATIONS = {
    ("bot_fi", "coin.362514.ir"): Path(
        "/etc/nginx/sites-available/coin.362514.ir"
    ),
    ("bot_fi", "mini-app.362514.ir"): Path(
        "/etc/nginx/sites-available/trading-bot"
    ),
    ("webapp_fi", "coin.gold-trade.ir"): Path(
        "/etc/nginx/sites-available/trading-bot"
    ),
}
LEGACY_UPSTREAMS_BY_VHOST = {
    "coin.362514.ir": ("http://127.0.0.1:8000",),
    "mini-app.362514.ir": ("http://127.0.0.1:8000",),
    "coin.gold-trade.ir": (
        "http://trading_bot_api",
        "http://127.0.0.1:8000",
    ),
}
MAX_PROXY_PASS_DIRECTIVES = 64
MUTATING_HOST_ACTIONS = frozenset(
    {"install", "test", "activate", "rollback-freeze", "restore"}
)
CONTROLLED_HOST_ACTIONS = MUTATING_HOST_ACTIONS | {"readback"}


class NginxGenerationError(RuntimeError):
    """Raised when a generation or host transition is not provably bounded."""


class NginxGenerationCancellation(NginxGenerationError):
    """The controller connection or host process authority was lost."""


class NginxCommandError(NginxGenerationError):
    """A bounded command failed and carries non-secret digest evidence."""

    def __init__(self, message: str, evidence: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.evidence = dict(evidence)


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int
    quoted: bool = False


@dataclass(frozen=True)
class Directive:
    name: str
    args: tuple[Token, ...]
    start: int
    end: int
    open_brace_end: int | None
    close_brace_start: int | None
    children: tuple["Directive", ...]

    @property
    def is_block(self) -> bool:
        return self.open_brace_end is not None


@dataclass(frozen=True)
class VhostSource:
    role: str
    vhost: str
    source_path: Path
    destination: Path
    legacy_upstreams: tuple[str, ...]
    legacy_static_root: str | None


@dataclass(frozen=True)
class HostLayout:
    system_root: Path = Path("/")
    operation_base: Path = DEFAULT_OPERATION_BASE
    nginx_bin: Path = DEFAULT_NGINX
    nginx_conf: Path = DEFAULT_NGINX_CONF
    sites_available: Path = DEFAULT_SITES_AVAILABLE
    sites_enabled: Path = DEFAULT_SITES_ENABLED
    reload_argv: tuple[str, ...] = DEFAULT_RELOAD_ARGV
    reload_stability_observations: int = (
        DEFAULT_RELOAD_STABILITY_OBSERVATIONS
    )
    reload_stability_interval_seconds: float = (
        DEFAULT_RELOAD_STABILITY_INTERVAL_SECONDS
    )
    owner_uid: int = 0
    identity_addresses: tuple[str, ...] | None = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


RunFn = Callable[[Sequence[str], int], CommandResult]


def _anonymous_read_pipe_identity(
    descriptor: int,
    *,
    label: str,
) -> tuple[int, int]:
    if type(descriptor) is not int or descriptor < 0:
        raise NginxGenerationError(f"{label} descriptor is invalid")
    try:
        metadata = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        target = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError as exc:
        raise NginxGenerationError(f"{label} pipe is unavailable") from exc
    if (
        not stat.S_ISFIFO(metadata.st_mode)
        or flags & os.O_ACCMODE != os.O_RDONLY
        or target != f"pipe:[{metadata.st_ino}]"
    ):
        raise NginxGenerationError(
            f"{label} must be an anonymous read-only pipe"
        )
    try:
        entries = tuple(Path("/proc/self/fd").iterdir())
    except OSError as exc:
        raise NginxGenerationError(
            f"{label} descriptor closure cannot be inspected"
        ) from exc
    for entry in entries:
        if not entry.name.isdecimal() or int(entry.name, 10) == descriptor:
            continue
        candidate = int(entry.name, 10)
        try:
            observed = os.fstat(candidate)
            observed_flags = fcntl.fcntl(candidate, fcntl.F_GETFL)
        except OSError:
            continue
        if (
            (observed.st_dev, observed.st_ino)
            == (metadata.st_dev, metadata.st_ino)
            and observed_flags & os.O_ACCMODE
            in {os.O_WRONLY, os.O_RDWR}
        ):
            raise NginxGenerationError(
                f"{label} writer end is held by the host worker"
            )
    return metadata.st_dev, metadata.st_ino


class ControllerLivenessGuard:
    """Keep host mutations bound to one controller-owned pipe."""

    _WAKE_SIGNAL = signal.SIGUSR1
    _HANDLED_SIGNALS = (
        signal.SIGHUP,
        signal.SIGTERM,
        signal.SIGINT,
        _WAKE_SIGNAL,
    )

    def __init__(self, control_fd: int) -> None:
        _anonymous_read_pipe_identity(
            control_fd,
            label="controller liveness",
        )
        if threading.current_thread() is not threading.main_thread():
            raise NginxGenerationError(
                "mutating Nginx host action must run in the main thread"
            )
        try:
            self._fd = os.dup(control_fd)
            os.set_inheritable(self._fd, False)
            os.set_blocking(self._fd, False)
        except OSError as exc:
            raise NginxGenerationError(
                "controller liveness pipe cannot be secured"
            ) from exc
        self._cancelled = threading.Event()
        self._stopping = threading.Event()
        self._reason = "controller liveness was lost"
        self._exception_delivered = False
        self._closed = False
        self._old_handlers: dict[int, Any] = {}
        self._monitor: threading.Thread | None = None

    @property
    def control_fd(self) -> int:
        return self._fd

    def _cancel(self, reason: str, *, wake_main: bool) -> None:
        if self._cancelled.is_set():
            return
        self._reason = reason
        self._cancelled.set()
        if wake_main:
            main_ident = threading.main_thread().ident
            if main_ident is not None:
                try:
                    signal.pthread_kill(main_ident, self._WAKE_SIGNAL)
                except (OSError, RuntimeError):
                    pass

    def _sample(self) -> None:
        selector = selectors.DefaultSelector()
        try:
            selector.register(self._fd, selectors.EVENT_READ)
            if not selector.select(0):
                return
            try:
                payload = os.read(self._fd, 1)
            except BlockingIOError:
                return
        finally:
            selector.close()
        reason = (
            "controller liveness pipe reached EOF"
            if payload == b""
            else "controller liveness pipe carried forbidden data"
        )
        self._cancel(reason, wake_main=False)
        self.check()

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        if self._exception_delivered:
            return
        self._exception_delivered = True
        if signum == self._WAKE_SIGNAL:
            reason = self._reason
        else:
            reason = f"Nginx host worker received signal {signum}"
            self._cancel(reason, wake_main=False)
        raise NginxGenerationCancellation(reason)

    def _monitor_control(self) -> None:
        selector = selectors.DefaultSelector()
        try:
            selector.register(self._fd, selectors.EVENT_READ)
            while not self._stopping.is_set():
                if not selector.select(PROCESS_POLL_SECONDS):
                    continue
                try:
                    payload = os.read(self._fd, 1)
                except BlockingIOError:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        return
                    payload = b""
                reason = (
                    "controller liveness pipe reached EOF"
                    if payload == b""
                    else "controller liveness pipe carried forbidden data"
                )
                self._cancel(reason, wake_main=True)
                return
        finally:
            selector.close()

    def __enter__(self) -> ControllerLivenessGuard:
        try:
            for signum in self._HANDLED_SIGNALS:
                self._old_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle_signal)
            self._sample()
            self._monitor = threading.Thread(
                target=self._monitor_control,
                name="nginx-generation-controller-liveness",
                daemon=True,
            )
            self._monitor.start()
            self.check()
            return self
        except BaseException:
            self._restore()
            raise

    def check(self) -> None:
        if self._cancelled.is_set() and not self._exception_delivered:
            self._exception_delivered = True
            raise NginxGenerationCancellation(self._reason)

    def _restore(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._exception_delivered = True
        self._stopping.set()
        if self._monitor is not None:
            self._monitor.join(timeout=1)
        try:
            os.close(self._fd)
        except OSError:
            pass
        for signum, handler in self._old_handlers.items():
            signal.signal(signum, handler)
        self._old_handlers.clear()

    def __exit__(
        self,
        error_type: Any,
        _value: Any,
        _traceback: Any,
    ) -> None:
        already_delivered = self._exception_delivered
        self._restore()
        if (
            error_type is None
            and self._cancelled.is_set()
            and not already_delivered
        ):
            raise NginxGenerationCancellation(self._reason)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise NginxGenerationError(f"{label} is not a nonzero SHA-256 digest")
    return value


def _readback_challenge(
    *,
    operation_id: str,
    role: str,
    expected_host: str,
    release_sha: str,
    release_tree_sha: str,
    manifest_sha256: str,
    archive_sha256: str,
    challenge_nonce: str | None,
    challenge_sha256: str | None,
    issued_at_epoch: int | None,
    expires_at_epoch: int | None,
    observed_at_epoch: int,
) -> tuple[dict[str, Any], str]:
    if (
        not isinstance(challenge_nonce, str)
        or SHA256_RE.fullmatch(challenge_nonce) is None
        or challenge_nonce == "0" * 64
    ):
        raise NginxGenerationError(
            "fresh readback challenge nonce is invalid"
        )
    challenge_sha256 = _nonzero_sha256(
        challenge_sha256,
        label="fresh readback challenge SHA-256",
    )
    if (
        type(issued_at_epoch) is not int
        or type(expires_at_epoch) is not int
        or type(observed_at_epoch) is not int
        or issued_at_epoch < 1
        or expires_at_epoch
        != issued_at_epoch + READBACK_CHALLENGE_TTL_SECONDS
        or observed_at_epoch
        < issued_at_epoch - READBACK_MAX_CLOCK_SKEW_SECONDS
        or observed_at_epoch > expires_at_epoch
    ):
        raise NginxGenerationError(
            "fresh readback challenge time window is invalid"
        )
    challenge = {
        "schema": HOST_READBACK_CHALLENGE_SCHEMA,
        "operation_id": operation_id,
        "role": role,
        "expected_host": expected_host,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "manifest_sha256": manifest_sha256,
        "archive_sha256": archive_sha256,
        "readback_challenge_nonce": challenge_nonce,
        "issued_at_epoch": issued_at_epoch,
        "expires_at_epoch": expires_at_epoch,
    }
    observed_sha256 = _sha256(canonical_json_bytes(challenge))
    if observed_sha256 != challenge_sha256:
        raise NginxGenerationError(
            "fresh readback challenge binding differs"
        )
    return challenge, observed_sha256


def _canonical_uuid4(value: str, *, label: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise NginxGenerationError(f"{label} is not a UUID") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise NginxGenerationError(f"{label} must be a canonical UUIDv4")
    return value


def _release_sha(value: str, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise NginxGenerationError(f"{label} must be a lowercase 40-hex identity")
    return value


def _tcp_port(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
        raise NginxGenerationError(f"{label} is outside the unprivileged TCP range")
    if value == 8000:
        raise NginxGenerationError(f"{label} collides with the legacy API listener")
    return value


def _shadow_release_root(
    operation_id: str,
    release_sha: str,
    value: Path,
) -> Path:
    expected = (
        Path("/srv/trading-bot-three-site-production-shadow")
        / operation_id
        / "releases"
        / release_sha
    )
    if value != expected:
        raise NginxGenerationError("shadow release root is not the canonical operation path")
    return value


def _safe_absolute_path(value: str | Path, *, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise NginxGenerationError(f"{label} is not a path")
    path = Path(value)
    pure = PurePosixPath(os.fspath(path))
    if not pure.is_absolute() or ".." in pure.parts or "\x00" in os.fspath(path):
        raise NginxGenerationError(f"{label} is not an absolute normalized path")
    if os.path.normpath(os.fspath(path)) != os.fspath(path):
        raise NginxGenerationError(f"{label} is not normalized")
    return path


def _read_stable_regular(
    path: Path,
    *,
    label: str,
    owner_uid: int,
    maximum: int = MAX_CONFIG_BYTES,
    private: bool = False,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NginxGenerationError(f"cannot securely open {label}: {path}") from exc
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum
            or mode & 0o022
            or (private and mode & 0o077)
        ):
            raise NginxGenerationError(f"{label} is not a safe root-owned regular file")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if len(payload) > maximum or any(
            getattr(before, field) != getattr(after, field) for field in stable
        ):
            raise NginxGenerationError(f"{label} changed during its stable read")
        return payload
    finally:
        os.close(descriptor)


def _write_new(path: Path, payload: bytes, *, label: str, maximum: int) -> None:
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=0o600,
            max_size=maximum,
        )
    except SecureFileError as exc:
        raise NginxGenerationError(str(exc)) from exc


def _write_new_or_verify(
    path: Path,
    payload: bytes,
    *,
    label: str,
    owner_uid: int,
    maximum: int,
) -> None:
    if path.exists() or path.is_symlink():
        observed = _read_stable_regular(
            path,
            label=label,
            owner_uid=owner_uid,
            maximum=maximum,
            private=True,
        )
        if observed != payload:
            raise NginxGenerationError(f"existing {label} differs from the operation")
        return
    _write_new(path, payload, label=label, maximum=maximum)


def _decode_token(raw: str, *, quoted: bool) -> str:
    if not quoted:
        return raw
    quote = raw[0]
    if len(raw) < 2 or raw[-1] != quote:
        raise NginxGenerationError("unterminated quoted Nginx token")
    output: list[str] = []
    index = 1
    while index < len(raw) - 1:
        char = raw[index]
        if char == "\\":
            index += 1
            if index >= len(raw) - 1:
                raise NginxGenerationError("invalid trailing escape in Nginx token")
            output.append(raw[index])
        else:
            output.append(char)
        index += 1
    return "".join(output)


def tokenize_nginx(text: str) -> tuple[Token, ...]:
    """Tokenize Nginx syntax while respecting comments, quotes, and escapes."""

    tokens: list[Token] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "#":
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline + 1
            continue
        if char in "{};":
            tokens.append(Token(char, index, index + 1))
            index += 1
            continue
        start = index
        if char in "\"'":
            quote = char
            index += 1
            escaped = False
            while index < len(text):
                current = text[index]
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    index += 1
                    break
                index += 1
            else:
                raise NginxGenerationError("unterminated quoted Nginx token")
            raw = text[start:index]
            tokens.append(
                Token(
                    _decode_token(raw, quoted=True),
                    start,
                    index,
                    quoted=True,
                )
            )
            continue
        escaped = False
        while index < len(text):
            current = text[index]
            if escaped:
                escaped = False
                index += 1
                continue
            if current == "\\":
                escaped = True
                index += 1
                continue
            if current.isspace() or current in "{};#":
                break
            index += 1
        if escaped:
            raise NginxGenerationError("invalid trailing escape in Nginx token")
        if start == index:
            raise NginxGenerationError("invalid Nginx token")
        raw = text[start:index]
        tokens.append(Token(raw.replace("\\ ", " "), start, index))
    return tuple(tokens)


def parse_nginx(text: str) -> tuple[Directive, ...]:
    tokens = tokenize_nginx(text)

    def parse_sequence(
        position: int,
        *,
        nested: bool,
        depth: int,
    ) -> tuple[tuple[Directive, ...], int]:
        if depth > 64:
            raise NginxGenerationError("Nginx block nesting exceeds the safety bound")
        nodes: list[Directive] = []
        while position < len(tokens):
            if tokens[position].value == "}":
                if not nested:
                    raise NginxGenerationError("unexpected closing brace in Nginx config")
                return tuple(nodes), position + 1
            header_start = position
            while position < len(tokens) and tokens[position].value not in {"{", "}", ";"}:
                position += 1
            if position == header_start:
                raise NginxGenerationError("empty or malformed Nginx directive")
            if position >= len(tokens):
                raise NginxGenerationError("unterminated Nginx directive")
            header = tokens[header_start:position]
            terminal = tokens[position]
            if terminal.value == "}":
                raise NginxGenerationError("Nginx directive is missing a terminator")
            if terminal.value == ";":
                nodes.append(
                    Directive(
                        name=header[0].value,
                        args=tuple(header[1:]),
                        start=header[0].start,
                        end=terminal.end,
                        open_brace_end=None,
                        close_brace_start=None,
                        children=(),
                    )
                )
                position += 1
                continue
            children, next_position = parse_sequence(
                position + 1,
                nested=True,
                depth=depth + 1,
            )
            close = tokens[next_position - 1]
            nodes.append(
                Directive(
                    name=header[0].value,
                    args=tuple(header[1:]),
                    start=header[0].start,
                    end=close.end,
                    open_brace_end=terminal.end,
                    close_brace_start=close.start,
                    children=children,
                )
            )
            position = next_position
        if nested:
            raise NginxGenerationError("unterminated Nginx block")
        return tuple(nodes), position

    parsed, final = parse_sequence(0, nested=False, depth=0)
    if final != len(tokens):
        raise NginxGenerationError("Nginx parser did not consume the document")
    return parsed


def _walk(nodes: Iterable[Directive]) -> Iterable[Directive]:
    for node in nodes:
        yield node
        yield from _walk(node.children)


def _direct(node: Directive, name: str) -> list[Directive]:
    return [child for child in node.children if child.name == name]


def _find_ssl_server(nodes: tuple[Directive, ...], vhost: str) -> Directive:
    matching: list[Directive] = []
    for node in _walk(nodes):
        if node.name != "server" or not node.is_block:
            continue
        names = _direct(node, "server_name")
        for directive in names:
            values = [token.value for token in directive.args]
            if vhost in values and values != [vhost]:
                raise NginxGenerationError(
                    f"{vhost} is mixed with aliases in a server_name directive"
                )
        exact = [item for item in names if [token.value for token in item.args] == [vhost]]
        if not exact:
            continue
        if len(exact) != 1 or len(names) != 1:
            raise NginxGenerationError(f"{vhost} has ambiguous server_name directives")
        ssl_listen = any(
            "ssl" in [token.value for token in directive.args]
            for directive in _direct(node, "listen")
        )
        ssl_material = bool(
            _direct(node, "ssl_certificate") or _direct(node, "ssl_certificate_key")
        )
        if ssl_listen or ssl_material:
            matching.append(node)
    if len(matching) != 1:
        raise NginxGenerationError(
            f"{vhost} must have exactly one unambiguous SSL server block"
        )
    return matching[0]


def _quote_nginx(value: str) -> str:
    if any(char in value for char in "\x00\r\n"):
        raise NginxGenerationError("Nginx value contains a forbidden control character")
    if re.fullmatch(r"[A-Za-z0-9_./:$-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _replace_spans(text: str, replacements: Iterable[tuple[int, int, str]]) -> str:
    ordered = sorted(replacements, key=lambda item: item[0], reverse=True)
    previous = len(text) + 1
    for start, end, replacement in ordered:
        if not (0 <= start <= end <= len(text)) or end > previous:
            raise NginxGenerationError("overlapping or invalid Nginx rewrite span")
        text = text[:start] + replacement + text[end:]
        previous = start
    return text


def _shadow_static_value(
    value: str,
    *,
    legacy_root: str,
    shadow_root: Path,
) -> str:
    legacy_prefix = legacy_root.rstrip("/")
    if value == legacy_prefix:
        return os.fspath(shadow_root)
    if value.startswith(legacy_prefix + "/"):
        suffix = value[len(legacy_prefix) :]
        if ".." in PurePosixPath(suffix).parts:
            raise NginxGenerationError("static alias contains a parent traversal")
        return os.fspath(shadow_root) + suffix
    raise NginxGenerationError("static root or alias leaves the expected legacy subtree")


def _parse_vhost_source(
    source: bytes,
    *,
    vhost: str,
) -> tuple[str, Directive]:
    if not source or len(source) > MAX_CONFIG_BYTES or b"\x00" in source:
        raise NginxGenerationError("Nginx source is empty, oversized, or contains NUL")
    if any(marker in source for marker in PEM_MARKERS):
        raise NginxGenerationError("Nginx source contains embedded TLS material")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NginxGenerationError("Nginx source is not UTF-8") from exc
    if MARKER in text:
        raise NginxGenerationError("Nginx source already contains a generation marker")
    return text, _find_ssl_server(parse_nginx(text), vhost)


def _canonical_legacy_upstreams(
    vhost: str,
    values: Sequence[str],
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise NginxGenerationError(
            f"{vhost} legacy upstream closure is not an ordered sequence"
        )
    declared = tuple(values)
    expected = LEGACY_UPSTREAMS_BY_VHOST.get(vhost)
    if expected is None:
        raise NginxGenerationError("legacy upstream closure vhost is unknown")
    if (
        not declared
        or any(not isinstance(value, str) or not value for value in declared)
    ):
        raise NginxGenerationError(
            f"{vhost} legacy upstream closure contains an invalid value"
        )
    if len(declared) != len(set(declared)):
        raise NginxGenerationError(
            f"{vhost} legacy upstream closure contains a duplicate"
        )
    if declared != expected:
        raise NginxGenerationError(
            f"{vhost} legacy upstream closure is outside the exact allowlist"
        )
    return expected


def _proxy_upstream_closure(
    server: Directive,
    *,
    vhost: str,
    legacy_upstreams: Sequence[str],
) -> tuple[list[Directive], tuple[str, ...], dict[str, int]]:
    expected = _canonical_legacy_upstreams(vhost, legacy_upstreams)
    directives = [
        item for item in _walk(server.children) if item.name == "proxy_pass"
    ]
    if not directives:
        raise NginxGenerationError(f"{vhost} has no API proxy_pass directives")
    if len(directives) > MAX_PROXY_PASS_DIRECTIVES:
        raise NginxGenerationError(
            f"{vhost} has too many API proxy_pass directives"
        )
    values: list[str] = []
    for item in directives:
        if len(item.args) != 1:
            raise NginxGenerationError(
                f"{vhost} contains an invalid API upstream directive"
            )
        value = item.args[0].value
        if value not in expected:
            raise NginxGenerationError(
                f"{vhost} contains an unknown API upstream"
            )
        values.append(value)
    if set(values) != set(expected):
        raise NginxGenerationError(
            f"{vhost} does not contain the exact legacy upstream closure"
        )
    occurrences = {
        upstream: values.count(upstream) for upstream in expected
    }
    if (
        set(occurrences) != set(expected)
        or any(count < 1 for count in occurrences.values())
        or sum(occurrences.values()) != len(values)
    ):
        raise NginxGenerationError(
            f"{vhost} legacy upstream occurrence closure is invalid"
        )
    return directives, tuple(values), occurrences


def render_generation(
    source: bytes,
    *,
    operation_id: str,
    vhost: str,
    state: str,
    legacy_upstreams: Sequence[str],
    legacy_static_root: str | None,
    shadow_api_port: int,
    shadow_static_root: Path,
) -> bytes:
    """Render exactly one state from an unmodified legacy source config."""

    if state not in GENERATION_STATES:
        raise NginxGenerationError("unknown Nginx generation state")
    text, server = _parse_vhost_source(source, vhost=vhost)
    proxy_directives, source_proxy_values, _ = (
        _proxy_upstream_closure(
            server,
            vhost=vhost,
            legacy_upstreams=legacy_upstreams,
        )
    )

    static_directives = [
        item for item in _walk(server.children) if item.name in {"root", "alias"}
    ]
    if legacy_static_root is None:
        if static_directives:
            raise NginxGenerationError(
                f"{vhost} contains an unexpected static root or alias"
            )
    else:
        if not static_directives:
            raise NginxGenerationError(f"{vhost} is missing its static root")
        for item in static_directives:
            if len(item.args) != 1:
                raise NginxGenerationError(
                    f"{vhost} contains an invalid static directive"
                )
            if item.name == "root" and item.args[0].value != legacy_static_root:
                raise NginxGenerationError(f"{vhost} contains an unknown static root")
            _shadow_static_value(
                item.args[0].value,
                legacy_root=legacy_static_root,
                shadow_root=shadow_static_root,
            )

    if state == "legacy-normal":
        return source

    replacements: list[tuple[int, int, str]] = []
    if state in {"shadow-readonly", "shadow-writable"}:
        replacement_upstream = f"http://127.0.0.1:{shadow_api_port}"
        replacements.extend(
            (item.args[0].start, item.args[0].end, replacement_upstream)
            for item in proxy_directives
        )
        replacements.extend(
            (
                item.args[0].start,
                item.args[0].end,
                _quote_nginx(
                    _shadow_static_value(
                        item.args[0].value,
                        legacy_root=str(legacy_static_root),
                        shadow_root=shadow_static_root,
                    )
                ),
            )
            for item in static_directives
        )

    indent = "\n    "
    marker = (
        f"{indent}{MARKER} {operation_id} {state} {vhost}\n"
    )
    block = ""
    if state in {"legacy-frozen", "shadow-readonly"}:
        block = (
            "    if ($request_method !~ ^(GET|HEAD|OPTIONS)$) {\n"
            "        return 503;\n"
            "    }\n"
            "    if ($http_upgrade != \"\") {\n"
            "        return 503;\n"
            "    }\n"
        )
    if server.open_brace_end is None:
        raise NginxGenerationError("target SSL server is not a block")
    replacements.append(
        (server.open_brace_end, server.open_brace_end, marker + block)
    )
    rendered = _replace_spans(text, replacements).encode("utf-8")

    reparsed = parse_nginx(rendered.decode("utf-8"))
    rendered_server = _find_ssl_server(reparsed, vhost)
    observed_proxy = [
        item for item in _walk(rendered_server.children) if item.name == "proxy_pass"
    ]
    expected_upstreams = (
        source_proxy_values
        if state == "legacy-frozen"
        else tuple(
            f"http://127.0.0.1:{shadow_api_port}"
            for _item in proxy_directives
        )
    )
    observed_proxy_values = tuple(
        item.args[0].value
        for item in observed_proxy
        if len(item.args) == 1
    )
    if (
        len(observed_proxy_values) != len(observed_proxy)
        or observed_proxy_values != expected_upstreams
    ):
        raise NginxGenerationError("rendered API upstream count or value differs")
    observed_static = [
        item
        for item in _walk(rendered_server.children)
        if item.name in {"root", "alias"}
    ]
    expected_static = [
        (
            item.name,
            item.args[0].value
            if state == "legacy-frozen"
            else _shadow_static_value(
                item.args[0].value,
                legacy_root=str(legacy_static_root),
                shadow_root=shadow_static_root,
            ),
        )
        for item in static_directives
    ]
    observed_static_values = [
        (item.name, item.args[0].value)
        for item in observed_static
        if len(item.args) == 1
    ]
    if (
        len(observed_static_values) != len(observed_static)
        or observed_static_values != expected_static
    ):
        raise NginxGenerationError("rendered static root count or value differs")
    expected_marker = f"{MARKER} {operation_id} {state} {vhost}"
    if rendered.decode("utf-8").count(expected_marker) != 1:
        raise NginxGenerationError("rendered generation marker count differs")
    return rendered


def default_sources(
    *,
    bot_coin_source: Path,
    bot_mini_source: Path,
    bot_mini_legacy_root: str,
    webapp_source: Path,
) -> tuple[VhostSource, ...]:
    bot_root = os.fspath(
        _safe_absolute_path(bot_mini_legacy_root, label="Bot-FI legacy static root")
    )
    return (
        VhostSource(
            role="bot_fi",
            vhost="coin.362514.ir",
            source_path=bot_coin_source,
            destination=FIXED_DESTINATIONS[("bot_fi", "coin.362514.ir")],
            legacy_upstreams=LEGACY_UPSTREAMS_BY_VHOST["coin.362514.ir"],
            legacy_static_root=None,
        ),
        VhostSource(
            role="bot_fi",
            vhost="mini-app.362514.ir",
            source_path=bot_mini_source,
            destination=FIXED_DESTINATIONS[("bot_fi", "mini-app.362514.ir")],
            legacy_upstreams=LEGACY_UPSTREAMS_BY_VHOST[
                "mini-app.362514.ir"
            ],
            legacy_static_root=bot_root,
        ),
        VhostSource(
            role="webapp_fi",
            vhost="coin.gold-trade.ir",
            source_path=webapp_source,
            destination=FIXED_DESTINATIONS[("webapp_fi", "coin.gold-trade.ir")],
            legacy_upstreams=LEGACY_UPSTREAMS_BY_VHOST[
                "coin.gold-trade.ir"
            ],
            legacy_static_root="/srv/trading-bot/current/mini_app_dist",
        ),
    )


def _validate_sources(sources: Sequence[VhostSource]) -> tuple[VhostSource, ...]:
    expected = set(FIXED_DESTINATIONS)
    observed = {(item.role, item.vhost) for item in sources}
    if len(sources) != 3 or observed != expected:
        raise NginxGenerationError("source set must contain the exact three production vhosts")
    for item in sources:
        if item.role not in ROLES:
            raise NginxGenerationError("source role is not a production Nginx role")
        if item.destination != FIXED_DESTINATIONS[(item.role, item.vhost)]:
            raise NginxGenerationError("source destination differs from fixed production mapping")
        _safe_absolute_path(item.destination, label="Nginx destination")
        _safe_absolute_path(item.source_path, label="Nginx source")
        if not isinstance(item.legacy_upstreams, tuple):
            raise NginxGenerationError(
                "legacy API upstream closure must be an immutable tuple"
            )
        _canonical_legacy_upstreams(
            item.vhost,
            item.legacy_upstreams,
        )
        if item.legacy_static_root is not None:
            _safe_absolute_path(item.legacy_static_root, label="legacy static root")
    return tuple(sorted(sources, key=lambda item: (item.role, item.vhost)))


def _archive_member_name(state: str, destination: Path) -> str:
    relative = destination.relative_to("/")
    return f"generations/{state}/{relative.as_posix()}"


def _deterministic_tar(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(files):
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or not name.startswith("generations/"):
                raise NginxGenerationError("archive member path is unsafe")
            payload = files[name]
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    payload = buffer.getvalue()
    if not payload or len(payload) > MAX_ARCHIVE_BYTES:
        raise NginxGenerationError("role generation archive is empty or oversized")
    return payload


def _generation_digest(rows: Mapping[str, str]) -> str:
    return _sha256(canonical_json_bytes(dict(sorted(rows.items()))))


def _legacy_upstream_closure_digest(
    rows: Sequence[Mapping[str, Any]],
) -> str:
    closure: dict[str, dict[str, Any]] = {}
    for row in rows:
        vhost = row["vhost"]
        if vhost in closure:
            raise NginxGenerationError(
                "legacy upstream closure repeats a vhost"
            )
        closure[vhost] = {
            "legacy_upstreams": list(row["legacy_upstreams"]),
            "legacy_upstream_occurrences": dict(
                sorted(row["legacy_upstream_occurrences"].items())
            ),
        }
    return _sha256(canonical_json_bytes(dict(sorted(closure.items()))))


def produce_generations(
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    shadow_release_root: Path,
    role_api_ports: Mapping[str, int],
    sources: Sequence[VhostSource],
    output_root: Path,
    owner_uid: int = 0,
) -> dict[str, Any]:
    operation_id = _canonical_uuid4(operation_id, label="operation_id")
    release_sha = _release_sha(release_sha, label="release_sha")
    release_tree_sha = _release_sha(release_tree_sha, label="release_tree_sha")
    if release_sha == release_tree_sha:
        raise NginxGenerationError("release commit and tree identities must differ")
    shadow_release_root = _shadow_release_root(
        operation_id, release_sha, shadow_release_root
    )
    if set(role_api_ports) != set(ROLES):
        raise NginxGenerationError("shadow API ports must bind exactly both Nginx roles")
    ports = {
        role: _tcp_port(role_api_ports[role], label=f"{role} shadow API port")
        for role in ROLES
    }
    if len(set(ports.values())) != len(ports):
        raise NginxGenerationError("shadow API ports must be distinct")
    sources = _validate_sources(sources)
    _safe_absolute_path(output_root, label="generation output root")
    if output_root.exists() or output_root.is_symlink():
        raise NginxGenerationError("generation output root must be create-only")
    output_root.mkdir(mode=0o700, parents=False)
    output_metadata = output_root.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(output_metadata.st_mode)
        or output_metadata.st_uid != owner_uid
        or stat.S_IMODE(output_metadata.st_mode) != 0o700
    ):
        raise NginxGenerationError("generation output root is not owner-only")

    rendered_by_role: dict[str, dict[str, dict[str, bytes]]] = {
        role: {state: {} for state in GENERATION_STATES} for role in ROLES
    }
    source_rows: dict[tuple[str, str], dict[str, Any]] = {}
    shadow_static_root = shadow_release_root / "mini_app_dist"
    for item in sources:
        source = _read_stable_regular(
            item.source_path,
            label=f"{item.role} {item.vhost} source",
            owner_uid=owner_uid,
        )
        _, source_server = _parse_vhost_source(
            source,
            vhost=item.vhost,
        )
        _, _, upstream_occurrences = (
            _proxy_upstream_closure(
                source_server,
                vhost=item.vhost,
                legacy_upstreams=item.legacy_upstreams,
            )
        )
        states: dict[str, bytes] = {}
        for state in GENERATION_STATES:
            states[state] = render_generation(
                source,
                operation_id=operation_id,
                vhost=item.vhost,
                state=state,
                legacy_upstreams=item.legacy_upstreams,
                legacy_static_root=item.legacy_static_root,
                shadow_api_port=ports[item.role],
                shadow_static_root=shadow_static_root,
            )
            rendered_by_role[item.role][state][os.fspath(item.destination)] = states[state]
        source_rows[(item.role, item.vhost)] = {
            "vhost": item.vhost,
            "destination": os.fspath(item.destination),
            "source_sha256": _sha256(source),
            "source_bytes": len(source),
            "legacy_upstreams": list(item.legacy_upstreams),
            "legacy_upstream_occurrences": upstream_occurrences,
            "legacy_static_root": item.legacy_static_root,
            "generation_sha256": {
                state: _sha256(states[state]) for state in GENERATION_STATES
            },
        }

    global_generation_rows: dict[str, dict[str, str]] = {
        state: {} for state in GENERATION_STATES
    }
    role_documents: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        role_directory = output_root / role
        role_directory.mkdir(mode=0o700)
        archive_files: dict[str, bytes] = {}
        for state in GENERATION_STATES:
            for destination, payload in rendered_by_role[role][state].items():
                archive_files[_archive_member_name(state, Path(destination))] = payload
                global_generation_rows[state][f"{role}:{destination}"] = _sha256(payload)
        archive_payload = _deterministic_tar(archive_files)
        archive_path = role_directory / "nginx-generations.tar"
        _write_new(
            archive_path,
            archive_payload,
            label=f"{role} Nginx generation archive",
            maximum=MAX_ARCHIVE_BYTES,
        )
        role_rows = [
            source_rows[(item.role, item.vhost)]
            for item in sources
            if item.role == role
        ]
        role_generation_sha256 = {
            state: _generation_digest(
                {
                    destination: _sha256(payload)
                    for destination, payload in rendered_by_role[role][state].items()
                }
            )
            for state in GENERATION_STATES
        }
        legacy_upstream_closure_sha256 = (
            _legacy_upstream_closure_digest(role_rows)
        )
        role_document: dict[str, Any] = {
            "schema": ROLE_MANIFEST_SCHEMA,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "release_tree_sha": release_tree_sha,
            "role": role,
            "expected_host": ROLE_HOSTS[role],
            "shadow_release_root": os.fspath(shadow_release_root),
            "shadow_static_root": os.fspath(shadow_static_root),
            "shadow_api_port": ports[role],
            "vhosts": role_rows,
            "legacy_upstream_closure_sha256": (
                legacy_upstream_closure_sha256
            ),
            "generation_sha256": role_generation_sha256,
            "nginx_legacy_normal_generation_sha256": role_generation_sha256[
                "legacy-normal"
            ],
            "nginx_rollback_generation_sha256": role_generation_sha256[
                "legacy-normal"
            ],
            "nginx_freeze_generation_sha256": role_generation_sha256[
                "legacy-frozen"
            ],
            "nginx_shadow_readonly_generation_sha256": role_generation_sha256[
                "shadow-readonly"
            ],
            "nginx_shadow_writable_generation_sha256": role_generation_sha256[
                "shadow-writable"
            ],
            "archive": {
                "format": ARCHIVE_FORMAT,
                "sha256": _sha256(archive_payload),
                "bytes": len(archive_payload),
                "members": sorted(archive_files),
            },
        }
        role_payload = canonical_json_bytes(role_document)
        manifest_path = role_directory / "nginx-generations-manifest.json"
        _write_new(
            manifest_path,
            role_payload,
            label=f"{role} Nginx generation manifest",
            maximum=MAX_JSON_BYTES,
        )
        role_documents[role] = {
            "manifest_sha256": _sha256(role_payload),
            "manifest_bytes": len(role_payload),
            "manifest_path": os.fspath(manifest_path),
            "archive_path": os.fspath(archive_path),
            "manifest": role_document,
        }

    global_digests = {
        state: _generation_digest(global_generation_rows[state])
        for state in GENERATION_STATES
    }
    aggregate: dict[str, Any] = {
        "schema": PRODUCER_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "shadow_release_root": os.fspath(shadow_release_root),
        "roles": {
            role: {
                "expected_host": ROLE_HOSTS[role],
                "manifest_sha256": role_documents[role]["manifest_sha256"],
                "manifest_bytes": role_documents[role]["manifest_bytes"],
                "archive_sha256": role_documents[role]["manifest"]["archive"]["sha256"],
                "archive_bytes": role_documents[role]["manifest"]["archive"]["bytes"],
                "legacy_upstream_closure_sha256": role_documents[role][
                    "manifest"
                ]["legacy_upstream_closure_sha256"],
                "generation_sha256": role_documents[role]["manifest"][
                    "generation_sha256"
                ],
            }
            for role in ROLES
        },
        "generation_sha256": global_digests,
        "legacy_upstream_closure_sha256": _sha256(
            canonical_json_bytes(
                {
                    role: role_documents[role]["manifest"][
                        "legacy_upstream_closure_sha256"
                    ]
                    for role in ROLES
                }
            )
        ),
        "nginx_legacy_normal_generation_sha256": global_digests["legacy-normal"],
        "nginx_rollback_generation_sha256": global_digests["legacy-normal"],
        "nginx_freeze_generation_sha256": global_digests["legacy-frozen"],
        "nginx_shadow_readonly_generation_sha256": global_digests[
            "shadow-readonly"
        ],
        "nginx_shadow_writable_generation_sha256": global_digests[
            "shadow-writable"
        ],
        "contains_tls_key_or_certificate_body": False,
        "production_contacted": False,
        "active_configuration_mutated": False,
    }
    aggregate_payload = canonical_json_bytes(aggregate)
    aggregate_path = output_root / "nginx-generation-aggregate.json"
    _write_new(
        aggregate_path,
        aggregate_payload,
        label="Nginx generation aggregate",
        maximum=MAX_JSON_BYTES,
    )
    return {
        **aggregate,
        "aggregate_sha256": _sha256(aggregate_payload),
        "aggregate_bytes": len(aggregate_payload),
        "aggregate_path": os.fspath(aggregate_path),
    }


def _read_strict_canonical_json(
    path: Path,
    *,
    label: str,
    owner_uid: int,
) -> tuple[dict[str, Any], bytes]:
    payload = _read_stable_regular(
        path,
        label=label,
        owner_uid=owner_uid,
        maximum=MAX_JSON_BYTES,
        private=True,
    )
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise NginxGenerationError(f"{label} is not strict JSON") from exc
    if not isinstance(document, dict) or payload != canonical_json_bytes(document):
        raise NginxGenerationError(f"{label} is not canonical JSON")
    return document, payload


def validate_role_manifest(
    document: Mapping[str, Any],
    *,
    manifest_sha256: str,
    expected_role: str,
    expected_host: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
) -> dict[str, Any]:
    expected_fields = {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "expected_host",
        "shadow_release_root",
        "shadow_static_root",
        "shadow_api_port",
        "vhosts",
        "legacy_upstream_closure_sha256",
        "generation_sha256",
        "nginx_legacy_normal_generation_sha256",
        "nginx_rollback_generation_sha256",
        "nginx_freeze_generation_sha256",
        "nginx_shadow_readonly_generation_sha256",
        "nginx_shadow_writable_generation_sha256",
        "archive",
    }
    if not isinstance(document, Mapping) or set(document) != expected_fields:
        raise NginxGenerationError("role generation manifest fields are not exact")
    canonical = canonical_json_bytes(document)
    if _sha256(canonical) != manifest_sha256:
        raise NginxGenerationError("role generation manifest hash differs")
    if (
        document["schema"] != ROLE_MANIFEST_SCHEMA
        or document["role"] != expected_role
        or document["expected_host"] != expected_host
        or document["operation_id"] != operation_id
        or document["release_sha"] != release_sha
        or document["release_tree_sha"] != release_tree_sha
        or ROLE_HOSTS.get(expected_role) != expected_host
    ):
        raise NginxGenerationError("role generation manifest identity differs")
    _canonical_uuid4(operation_id, label="operation_id")
    _release_sha(release_sha, label="release_sha")
    _release_sha(release_tree_sha, label="release_tree_sha")
    if release_tree_sha == release_sha:
        raise NginxGenerationError("release commit and tree identities must differ")
    _shadow_release_root(
        operation_id,
        release_sha,
        Path(str(document["shadow_release_root"])),
    )
    expected_static = Path(str(document["shadow_release_root"])) / "mini_app_dist"
    if document["shadow_static_root"] != os.fspath(expected_static):
        raise NginxGenerationError("role manifest shadow static root differs")
    _tcp_port(document["shadow_api_port"], label="shadow API port")
    expected_vhosts = {
        vhost for role, vhost in FIXED_DESTINATIONS if role == expected_role
    }
    vhosts = document["vhosts"]
    if (
        not isinstance(vhosts, list)
        or len(vhosts) != len(expected_vhosts)
        or {row.get("vhost") for row in vhosts if isinstance(row, dict)}
        != expected_vhosts
    ):
        raise NginxGenerationError("role manifest vhost inventory is not exact")
    if [row["vhost"] for row in vhosts] != sorted(expected_vhosts):
        raise NginxGenerationError("role manifest vhost ordering is not deterministic")
    destinations: set[str] = set()
    generation_rows: dict[str, dict[str, str]] = {
        state: {} for state in GENERATION_STATES
    }
    vhost_fields = {
        "vhost",
        "destination",
        "source_sha256",
        "source_bytes",
        "legacy_upstreams",
        "legacy_upstream_occurrences",
        "legacy_static_root",
        "generation_sha256",
    }
    for row in vhosts:
        if not isinstance(row, dict) or set(row) != vhost_fields:
            raise NginxGenerationError("role manifest vhost row fields are not exact")
        key = (expected_role, row["vhost"])
        if row["destination"] != os.fspath(FIXED_DESTINATIONS[key]):
            raise NginxGenerationError("role manifest destination mapping differs")
        if row["destination"] in destinations:
            raise NginxGenerationError("role manifest repeats a destination")
        destinations.add(row["destination"])
        _nonzero_sha256(row["source_sha256"], label="source_sha256")
        if (
            isinstance(row["source_bytes"], bool)
            or not isinstance(row["source_bytes"], int)
            or not 1 <= row["source_bytes"] <= MAX_CONFIG_BYTES
            or not isinstance(row["generation_sha256"], dict)
            or set(row["generation_sha256"]) != set(GENERATION_STATES)
        ):
            raise NginxGenerationError("role manifest source size or generations differ")
        for state in GENERATION_STATES:
            digest = _nonzero_sha256(
                row["generation_sha256"][state],
                label=f"{row['vhost']} {state}",
            )
            generation_rows[state][row["destination"]] = digest
        if len(set(row["generation_sha256"].values())) != len(GENERATION_STATES):
            raise NginxGenerationError("per-vhost generation hashes must be distinct")
        if row["generation_sha256"]["legacy-normal"] != row["source_sha256"]:
            raise NginxGenerationError("legacy-normal generation differs from source")
        expected_upstreams = LEGACY_UPSTREAMS_BY_VHOST[row["vhost"]]
        if row["legacy_upstreams"] != list(expected_upstreams):
            raise NginxGenerationError(
                f"{row['vhost']} role manifest legacy upstream closure differs"
            )
        occurrences = row["legacy_upstream_occurrences"]
        if (
            not isinstance(occurrences, dict)
            or set(occurrences) != set(expected_upstreams)
            or any(
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 1
                for count in occurrences.values()
            )
            or not 1
            <= sum(occurrences.values())
            <= MAX_PROXY_PASS_DIRECTIVES
        ):
            raise NginxGenerationError(
                f"{row['vhost']} role manifest upstream occurrences differ"
            )
        if key == ("bot_fi", "coin.362514.ir") and (
            row["legacy_static_root"] is not None
        ):
            raise NginxGenerationError("Bot-FI API vhost legacy binding differs")
        if key == ("bot_fi", "mini-app.362514.ir"):
            _safe_absolute_path(
                row["legacy_static_root"],
                label="Bot-FI mini-app legacy static root",
            )
        if key == ("webapp_fi", "coin.gold-trade.ir") and (
            row["legacy_static_root"]
            != "/srv/trading-bot/current/mini_app_dist"
        ):
            raise NginxGenerationError("WebApp-FI legacy vhost binding differs")
    expected_closure_sha256 = _legacy_upstream_closure_digest(vhosts)
    if (
        _nonzero_sha256(
            document["legacy_upstream_closure_sha256"],
            label="legacy upstream closure",
        )
        != expected_closure_sha256
    ):
        raise NginxGenerationError(
            "role manifest legacy upstream closure hash differs"
        )
    generation_sha256 = document["generation_sha256"]
    if not isinstance(generation_sha256, dict) or set(generation_sha256) != set(
        GENERATION_STATES
    ):
        raise NginxGenerationError("role aggregate generation fields differ")
    expected_generation = {
        state: _generation_digest(generation_rows[state])
        for state in GENERATION_STATES
    }
    if generation_sha256 != expected_generation:
        raise NginxGenerationError("role aggregate generation hashes differ")
    if len(set(generation_sha256.values())) != len(GENERATION_STATES):
        raise NginxGenerationError("role aggregate generation hashes must be distinct")
    aliases = {
        "nginx_legacy_normal_generation_sha256": "legacy-normal",
        "nginx_rollback_generation_sha256": "legacy-normal",
        "nginx_freeze_generation_sha256": "legacy-frozen",
        "nginx_shadow_readonly_generation_sha256": "shadow-readonly",
        "nginx_shadow_writable_generation_sha256": "shadow-writable",
    }
    if any(document[field] != expected_generation[state] for field, state in aliases.items()):
        raise NginxGenerationError("role cutover generation aliases differ")
    archive = document["archive"]
    if (
        not isinstance(archive, dict)
        or set(archive) != {"format", "sha256", "bytes", "members"}
        or archive["format"] != ARCHIVE_FORMAT
        or isinstance(archive["bytes"], bool)
        or not isinstance(archive["bytes"], int)
        or not 1 <= archive["bytes"] <= MAX_ARCHIVE_BYTES
        or not isinstance(archive["members"], list)
    ):
        raise NginxGenerationError("role archive metadata is invalid")
    _nonzero_sha256(archive["sha256"], label="archive sha256")
    expected_members = sorted(
        _archive_member_name(state, Path(destination))
        for state in GENERATION_STATES
        for destination in destinations
    )
    if archive["members"] != expected_members:
        raise NginxGenerationError("role archive member inventory differs")
    return json.loads(canonical.decode("utf-8"))


def _read_archive(
    path: Path,
    *,
    manifest: Mapping[str, Any],
    owner_uid: int,
) -> dict[str, bytes]:
    payload = _read_stable_regular(
        path,
        label="role generation archive",
        owner_uid=owner_uid,
        maximum=MAX_ARCHIVE_BYTES,
        private=True,
    )
    archive_meta = manifest["archive"]
    if len(payload) != archive_meta["bytes"] or _sha256(payload) != archive_meta["sha256"]:
        raise NginxGenerationError("role generation archive bytes or hash differ")
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            infos = archive.getmembers()
            if [item.name for item in infos] != archive_meta["members"]:
                raise NginxGenerationError("role generation archive order differs")
            for info in infos:
                pure = PurePosixPath(info.name)
                if (
                    not info.isfile()
                    or info.issym()
                    or info.islnk()
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or stat.S_IMODE(info.mode) != 0o600
                    or info.uid != 0
                    or info.gid != 0
                    or info.size < 1
                    or info.size > MAX_CONFIG_BYTES
                    or info.name in members
                ):
                    raise NginxGenerationError("role archive has an unsafe member")
                extracted = archive.extractfile(info)
                if extracted is None:
                    raise NginxGenerationError("role archive member cannot be read")
                member_payload = extracted.read(MAX_CONFIG_BYTES + 1)
                if len(member_payload) != info.size:
                    raise NginxGenerationError("role archive member size differs")
                members[info.name] = member_payload
    except (tarfile.TarError, OSError) as exc:
        raise NginxGenerationError("role generation archive is invalid") from exc
    if _deterministic_tar(members) != payload:
        raise NginxGenerationError("role generation archive encoding is not deterministic")
    expected_hashes = {
        _archive_member_name(state, Path(row["destination"])): row[
            "generation_sha256"
        ][state]
        for row in manifest["vhosts"]
        for state in GENERATION_STATES
    }
    if set(members) != set(expected_hashes) or any(
        _sha256(payload) != expected_hashes[name] for name, payload in members.items()
    ):
        raise NginxGenerationError("role archive member content differs from manifest")
    shadow_static_root = Path(str(manifest["shadow_static_root"]))
    for row in manifest["vhosts"]:
        destination = Path(row["destination"])
        source = members[
            _archive_member_name("legacy-normal", destination)
        ]
        _, source_server = _parse_vhost_source(
            source,
            vhost=row["vhost"],
        )
        _, _, occurrences = _proxy_upstream_closure(
            source_server,
            vhost=row["vhost"],
            legacy_upstreams=row["legacy_upstreams"],
        )
        if occurrences != row["legacy_upstream_occurrences"]:
            raise NginxGenerationError(
                "role archive legacy upstream occurrence closure differs"
            )
        for state in GENERATION_STATES:
            expected = render_generation(
                source,
                operation_id=manifest["operation_id"],
                vhost=row["vhost"],
                state=state,
                legacy_upstreams=row["legacy_upstreams"],
                legacy_static_root=row["legacy_static_root"],
                shadow_api_port=manifest["shadow_api_port"],
                shadow_static_root=shadow_static_root,
            )
            if members[_archive_member_name(state, destination)] != expected:
                raise NginxGenerationError(
                    "role archive generation differs from exact source rendering"
                )
    return members


def load_role_material(
    *,
    manifest_path: Path,
    expected_manifest_sha256: str,
    archive_path: Path,
    expected_role: str,
    expected_host: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    owner_uid: int,
) -> tuple[dict[str, Any], bytes, dict[str, bytes]]:
    expected_manifest_sha256 = _nonzero_sha256(
        expected_manifest_sha256, label="expected manifest"
    )
    document, payload = _read_strict_canonical_json(
        manifest_path,
        label="role generation manifest",
        owner_uid=owner_uid,
    )
    manifest = validate_role_manifest(
        document,
        manifest_sha256=expected_manifest_sha256,
        expected_role=expected_role,
        expected_host=expected_host,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
    )
    members = _read_archive(archive_path, manifest=manifest, owner_uid=owner_uid)
    return manifest, payload, members


def _ensure_private_directory(path: Path, *, owner_uid: int, create: bool) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise NginxGenerationError(f"required operation directory is absent: {path}")
        parent = path.parent
        parent_meta = parent.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent_meta.st_mode)
            or parent_meta.st_uid != owner_uid
            or stat.S_IMODE(parent_meta.st_mode) & 0o022
        ):
            raise NginxGenerationError("operation directory parent is unsafe")
        path.mkdir(mode=0o700)
        metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise NginxGenerationError(f"operation directory is not owner-only: {path}")


def _operation_root(layout: HostLayout, operation_id: str, role: str) -> Path:
    return layout.operation_base / operation_id / role.replace("_", "-")


def _stage_path(root: Path, member_name: str) -> Path:
    pure = PurePosixPath(member_name)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or pure.parts[0] != "generations"
        or len(pure.parts) < 4
    ):
        raise NginxGenerationError("archive member cannot map into operation staging")
    target = root.joinpath(*pure.parts)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise NginxGenerationError("archive member escapes operation staging") from exc
    return target


def _new_journal(manifest: Mapping[str, Any], manifest_sha256: str) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "operation_id": manifest["operation_id"],
        "role": manifest["role"],
        "expected_host": manifest["expected_host"],
        "release_sha": manifest["release_sha"],
        "manifest_sha256": manifest_sha256,
        "archive_sha256": manifest["archive"]["sha256"],
        "installed": True,
        "tested_states": {},
        "active_state": "legacy-normal",
        "transaction": None,
        "events": [],
        "state_sha256": "",
    }
    state["state_sha256"] = _journal_hash(state)
    return state


def _journal_hash(state: Mapping[str, Any]) -> str:
    unsigned = dict(state)
    unsigned["state_sha256"] = ""
    return _sha256(canonical_json_bytes(unsigned))


def _validate_journal(
    state: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
) -> dict[str, Any]:
    fields = {
        "schema",
        "operation_id",
        "role",
        "expected_host",
        "release_sha",
        "manifest_sha256",
        "archive_sha256",
        "installed",
        "tested_states",
        "active_state",
        "transaction",
        "events",
        "state_sha256",
    }
    expected = {
        "schema": JOURNAL_SCHEMA,
        "operation_id": manifest["operation_id"],
        "role": manifest["role"],
        "expected_host": manifest["expected_host"],
        "release_sha": manifest["release_sha"],
        "manifest_sha256": manifest_sha256,
        "archive_sha256": manifest["archive"]["sha256"],
        "installed": True,
    }
    tested_states = state.get("tested_states") if isinstance(state, Mapping) else None
    events = state.get("events") if isinstance(state, Mapping) else None
    events_valid = isinstance(events, list)
    previous_event_sha256 = "0" * 64
    if events_valid:
        for index, event in enumerate(events, 1):
            if (
                not isinstance(event, dict)
                or event.get("index") != index
                or not isinstance(event.get("kind"), str)
                or not event["kind"]
                or event.get("previous_event_sha256") != previous_event_sha256
                or not isinstance(event.get("event_sha256"), str)
            ):
                events_valid = False
                break
            unsigned_event = dict(event)
            observed_event_sha256 = unsigned_event.pop("event_sha256")
            if observed_event_sha256 != _sha256(canonical_json_bytes(unsigned_event)):
                events_valid = False
                break
            previous_event_sha256 = observed_event_sha256
    if (
        not isinstance(state, Mapping)
        or set(state) != fields
        or any(state.get(key) != value for key, value in expected.items())
        or not isinstance(tested_states, dict)
        or any(key not in GENERATION_STATES for key in tested_states)
        or any(
            not isinstance(value, dict)
            or set(value) != {"inventory_sha256", "candidate_sha256"}
            or any(
                not isinstance(digest, str)
                or SHA256_RE.fullmatch(digest) is None
                or digest == "0" * 64
                for digest in value.values()
            )
            for value in tested_states.values()
        )
        or state["active_state"] not in GENERATION_STATES
        or not events_valid
        or state["state_sha256"] != _journal_hash(state)
    ):
        raise NginxGenerationError("host journal is invalid or bound elsewhere")
    transaction = state["transaction"]
    if transaction is not None:
        base_fields = {
            "from_state",
            "to_state",
            "status",
            "inventory_sha256",
        }
        status = transaction.get("status") if isinstance(transaction, dict) else None
        expected_transaction_fields = (
            base_fields | {"rollback_reason", "failure_evidence"}
            if status
            in {"rolling-back", "rollback-validating", "rollback-failed"}
            else base_fields
        )
        if status == "rollback-failed":
            expected_transaction_fields.add("rollback_failure_evidence")
        if (
            not isinstance(transaction, dict)
            or set(transaction) != expected_transaction_fields
            or transaction["from_state"] not in GENERATION_STATES
            or transaction["to_state"] not in GENERATION_STATES
            or transaction["from_state"] == transaction["to_state"]
            or status
            not in {
                "prepared",
                "applying",
                "validating",
                "rolling-back",
                "rollback-validating",
                "rollback-failed",
            }
            or not isinstance(transaction["inventory_sha256"], str)
            or SHA256_RE.fullmatch(transaction["inventory_sha256"]) is None
            or transaction["inventory_sha256"] == "0" * 64
            or (
                str(status).startswith("rollback")
                and not isinstance(transaction.get("rollback_reason"), str)
            )
            or (
                str(status).startswith("rollback")
                and transaction.get("failure_evidence") is not None
                and not isinstance(transaction.get("failure_evidence"), dict)
            )
            or (
                status == "rollback-failed"
                and transaction.get("rollback_failure_evidence") is not None
                and not isinstance(
                    transaction.get("rollback_failure_evidence"),
                    dict,
                )
            )
        ):
            raise NginxGenerationError("pending Nginx transaction is invalid")
    return json.loads(canonical_json_bytes(state))


def _write_journal(path: Path, state: dict[str, Any], *, create: bool) -> None:
    state["state_sha256"] = _journal_hash(state)
    payload = canonical_json_bytes(state)
    try:
        if create:
            write_secure_new_bytes(
                path,
                payload,
                label="Nginx host journal",
                mode=0o600,
                max_size=MAX_JSON_BYTES,
            )
        else:
            write_secure_atomic_bytes(
                path,
                payload,
                label="Nginx host journal",
                mode=0o600,
                max_size=MAX_JSON_BYTES,
            )
    except SecureFileError as exc:
        raise NginxGenerationError("Nginx host journal could not be persisted") from exc


def _load_journal(
    path: Path,
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    owner_uid: int,
) -> dict[str, Any]:
    document, _ = _read_strict_canonical_json(
        path,
        label="Nginx host journal",
        owner_uid=owner_uid,
    )
    return _validate_journal(
        document,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
    )


def _append_event(journal: dict[str, Any], kind: str, **bindings: Any) -> None:
    event = {
        "index": len(journal["events"]) + 1,
        "kind": kind,
        **bindings,
        "previous_event_sha256": (
            journal["events"][-1]["event_sha256"]
            if journal["events"]
            else "0" * 64
        ),
    }
    event["event_sha256"] = _sha256(canonical_json_bytes(event))
    journal["events"].append(event)


def _journal_lock(root: Path, *, owner_uid: int):  # noqa: ANN202
    class _Lock:
        descriptor = -1

        def __enter__(self):  # noqa: ANN204
            path = root / "journal.lock"
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                self.descriptor = os.open(path, flags, 0o600)
            except OSError as exc:
                raise NginxGenerationError("Nginx journal lock is unavailable") from exc
            metadata = os.fstat(self.descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != owner_uid
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                os.close(self.descriptor)
                self.descriptor = -1
                raise NginxGenerationError("Nginx journal lock is unsafe")
            fcntl.flock(self.descriptor, fcntl.LOCK_EX)
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001, ANN204
            if self.descriptor >= 0:
                os.close(self.descriptor)
            return False

    return _Lock()


def _destination_payloads(
    manifest: Mapping[str, Any],
    members: Mapping[str, bytes],
    state: str,
    *,
    layout: HostLayout,
) -> dict[Path, bytes]:
    return {
        _host_path(Path(row["destination"]), layout=layout): members[
            _archive_member_name(state, Path(row["destination"]))
        ]
        for row in manifest["vhosts"]
    }


def _host_path(path: Path, *, layout: HostLayout) -> Path:
    if not path.is_absolute():
        raise NginxGenerationError("logical host path is not absolute")
    if layout.system_root == Path("/"):
        return path
    _safe_absolute_path(layout.system_root, label="injected system root")
    return layout.system_root / path.relative_to("/")


def _local_ipv4_addresses() -> set[str]:
    addresses: set[str] = set()
    try:
        interfaces = socket.if_nameindex()
        handle = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as exc:
        raise NginxGenerationError("cannot inspect local host network identity") from exc
    try:
        for _, name in interfaces:
            try:
                packed = struct.pack("256s", name.encode("ascii")[:15])
                result = fcntl.ioctl(handle.fileno(), 0x8915, packed)
            except (OSError, UnicodeEncodeError):
                continue
            addresses.add(socket.inet_ntoa(result[20:24]))
    finally:
        handle.close()
    if not addresses:
        raise NginxGenerationError("local host has no observable IPv4 identity")
    return addresses


def _active_state(
    manifest: Mapping[str, Any],
    members: Mapping[str, bytes],
    *,
    layout: HostLayout,
    owner_uid: int,
) -> str:
    observed = {
        row["destination"]: _sha256(
            _read_stable_regular(
                _host_path(Path(row["destination"]), layout=layout),
                label=f"active Nginx destination {row['destination']}",
                owner_uid=owner_uid,
            )
        )
        for row in manifest["vhosts"]
    }
    matches = [
        state
        for state in GENERATION_STATES
        if all(
            observed[row["destination"]] == row["generation_sha256"][state]
            for row in manifest["vhosts"]
        )
    ]
    if len(matches) != 1:
        raise NginxGenerationError("active Nginx destinations are mixed or foreign")
    return matches[0]


def _enabled_inventory(
    manifest: Mapping[str, Any],
    *,
    layout: HostLayout,
) -> tuple[list[dict[str, Any]], str]:
    metadata = layout.sites_enabled.stat(follow_symlinks=False)
    available_metadata = layout.sites_available.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != layout.owner_uid
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not stat.S_ISDIR(available_metadata.st_mode)
        or available_metadata.st_uid != layout.owner_uid
        or stat.S_IMODE(available_metadata.st_mode) & 0o022
    ):
        raise NginxGenerationError("Nginx site inventory roots are unsafe")
    target_destinations = {
        _host_path(Path(row["destination"]), layout=layout)
        for row in manifest["vhosts"]
    }
    target_counts = {destination: 0 for destination in target_destinations}
    rows: list[dict[str, Any]] = []
    for entry in sorted(layout.sites_enabled.iterdir(), key=lambda item: item.name):
        if "/" in entry.name or entry.name in {".", ".."}:
            raise NginxGenerationError("sites-enabled contains an invalid name")
        item_meta = entry.lstat()
        if item_meta.st_uid != layout.owner_uid or item_meta.st_nlink != 1:
            raise NginxGenerationError("sites-enabled entry ownership or link count differs")
        if stat.S_ISLNK(item_meta.st_mode):
            link_text = os.readlink(entry)
            if "\x00" in link_text:
                raise NginxGenerationError("sites-enabled symlink target is invalid")
            resolved = (entry.parent / link_text).resolve(strict=True)
            try:
                resolved.relative_to(layout.sites_available.resolve(strict=True))
            except ValueError as exc:
                raise NginxGenerationError(
                    "sites-enabled symlink escapes sites-available"
                ) from exc
            kind = "symlink"
        elif stat.S_ISREG(item_meta.st_mode):
            resolved = entry
            link_text = None
            kind = "regular"
        else:
            raise NginxGenerationError("sites-enabled contains an unsupported entry")
        payload = _read_stable_regular(
            resolved,
            label=f"enabled Nginx site {entry.name}",
            owner_uid=layout.owner_uid,
        )
        item_after = entry.lstat()
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
        )
        if any(
            getattr(item_meta, field) != getattr(item_after, field)
            for field in stable_fields
        ) or (
            kind == "symlink" and os.readlink(entry) != link_text
        ):
            raise NginxGenerationError("sites-enabled entry changed during inventory")
        resolved_normalized = Path(os.path.normpath(os.fspath(resolved)))
        managed_destination = None
        if resolved_normalized in target_counts:
            target_counts[resolved_normalized] += 1
            managed_destination = next(
                row["destination"]
                for row in manifest["vhosts"]
                if _host_path(Path(row["destination"]), layout=layout)
                == resolved_normalized
            )
        rows.append(
            {
                "name": entry.name,
                "kind": kind,
                "link_target": link_text,
                "resolved_path": os.fspath(resolved_normalized),
                "sha256": _sha256(payload),
                "managed_destination": managed_destination,
            }
        )
    if not rows or any(count != 1 for count in target_counts.values()):
        raise NginxGenerationError(
            "each managed destination must have exactly one enabled-site entry"
        )
    top = _read_stable_regular(
        layout.nginx_conf,
        label="top-level Nginx configuration",
        owner_uid=layout.owner_uid,
    )
    binding_rows = [
        {
            **row,
            "sha256": None if row["managed_destination"] is not None else row["sha256"],
        }
        for row in rows
    ]
    binding = {
        "top_level_sha256": _sha256(top),
        "enabled_sites": binding_rows,
    }
    return rows, _sha256(canonical_json_bytes(binding))


def _replace_enabled_include(
    top: bytes,
    *,
    sites_enabled: Path,
    candidate_include: Path,
) -> bytes:
    try:
        text = top.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NginxGenerationError("top-level Nginx config is not UTF-8") from exc
    parsed = parse_nginx(text)
    expected = os.fspath(sites_enabled / "*")
    matches = [
        item
        for item in _walk(parsed)
        if item.name == "include"
        and len(item.args) == 1
        and item.args[0].value == expected
    ]
    if len(matches) != 1:
        raise NginxGenerationError(
            "top-level Nginx config must have one exact sites-enabled include"
        )
    token = matches[0].args[0]
    return _replace_spans(
        text,
        [(token.start, token.end, _quote_nginx(os.fspath(candidate_include)))],
    ).encode("utf-8")


def _candidate_files(
    *,
    root: Path,
    state: str,
    manifest: Mapping[str, Any],
    members: Mapping[str, bytes],
    inventory: Sequence[Mapping[str, Any]],
    layout: HostLayout,
) -> tuple[Path, str]:
    candidate_root = root / "candidates" / state
    candidate_root.parent.mkdir(mode=0o700, exist_ok=True)
    if not candidate_root.exists():
        candidate_root.mkdir(mode=0o700)
    _ensure_private_directory(candidate_root, owner_uid=layout.owner_uid, create=False)
    targets = {
        os.fspath(
            _host_path(Path(row["destination"]), layout=layout)
        ): _stage_path(
            root,
            _archive_member_name(state, Path(row["destination"])),
        )
        for row in manifest["vhosts"]
    }
    include_lines: list[str] = []
    for row in inventory:
        resolved = row["resolved_path"]
        include_path = targets.get(resolved, layout.sites_enabled / row["name"])
        include_lines.append(f"include {_quote_nginx(os.fspath(include_path))};\n")
    include_payload = "".join(include_lines).encode("utf-8")
    include_path = candidate_root / "sites-enabled.conf"
    _write_new_or_verify(
        include_path,
        include_payload,
        label="candidate enabled-sites include",
        owner_uid=layout.owner_uid,
        maximum=MAX_CONFIG_BYTES,
    )
    top = _read_stable_regular(
        layout.nginx_conf,
        label="top-level Nginx configuration",
        owner_uid=layout.owner_uid,
    )
    candidate_top_payload = _replace_enabled_include(
        top,
        sites_enabled=layout.sites_enabled,
        candidate_include=include_path,
    )
    candidate_top_path = candidate_root / "nginx.conf"
    _write_new_or_verify(
        candidate_top_path,
        candidate_top_payload,
        label="candidate top-level Nginx configuration",
        owner_uid=layout.owner_uid,
        maximum=MAX_CONFIG_BYTES,
    )
    candidate_binding = {
        "state": state,
        "top_sha256": _sha256(candidate_top_payload),
        "include_sha256": _sha256(include_payload),
        "members": {
            row["destination"]: row["generation_sha256"][state]
            for row in manifest["vhosts"]
        },
    }
    return candidate_top_path, _sha256(canonical_json_bytes(candidate_binding))


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent_pid: int
    process_group: int
    start_time: int
    state: str

    @property
    def key(self) -> tuple[int, int]:
        return self.pid, self.start_time


def _process_identity(pid: int) -> ProcessIdentity | None:
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = payload[payload.rindex(") ") + 2 :].split()
        if len(fields) < 20:
            return None
        return ProcessIdentity(
            pid=pid,
            parent_pid=int(fields[1], 10),
            process_group=int(fields[2], 10),
            start_time=int(fields[19], 10),
            state=fields[0],
        )
    except (OSError, UnicodeError, ValueError):
        return None


def _process_snapshot() -> dict[int, ProcessIdentity]:
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError as exc:
        raise NginxGenerationError(
            "subprocess ownership inventory is unavailable"
        ) from exc
    return {
        identity.pid: identity
        for entry in entries
        if entry.name.isdecimal()
        for identity in (_process_identity(int(entry.name, 10)),)
        if identity is not None
    }


def _enable_child_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise NginxGenerationError(
            f"child subreaper setup failed with errno {error}"
        )


def _direct_child_baseline() -> frozenset[tuple[int, int]]:
    owner = os.getpid()
    return frozenset(
        identity.key
        for identity in _process_snapshot().values()
        if identity.parent_pid == owner
    )


def _owned_processes(
    root: ProcessIdentity,
    *,
    baseline_children: frozenset[tuple[int, int]],
) -> set[ProcessIdentity]:
    snapshot = _process_snapshot()
    observed_root = snapshot.get(root.pid)
    owned_ids: set[int] = set()
    if (
        observed_root is not None
        and observed_root.start_time == root.start_time
    ):
        owned_ids.add(root.pid)
    changed = True
    while changed:
        changed = False
        for identity in snapshot.values():
            if (
                identity.pid not in owned_ids
                and identity.parent_pid in owned_ids
            ):
                owned_ids.add(identity.pid)
                changed = True
    owner = os.getpid()
    for identity in snapshot.values():
        if (
            identity.parent_pid == owner
            and identity.key not in baseline_children
        ):
            owned_ids.add(identity.pid)
    return {
        identity
        for pid, identity in snapshot.items()
        if pid in owned_ids
    }


def _identity_is_live(identity: ProcessIdentity) -> bool:
    current = _process_identity(identity.pid)
    return (
        current is not None
        and current.start_time == identity.start_time
        and current.state != "Z"
    )


def _identity_is_current(identity: ProcessIdentity) -> bool:
    current = _process_identity(identity.pid)
    return (
        current is not None
        and current.start_time == identity.start_time
    )


def _reap_adopted_zombies(
    tracked: set[ProcessIdentity],
    *,
    root_pid: int,
) -> None:
    for identity in tuple(tracked):
        if identity.pid == root_pid:
            continue
        current = _process_identity(identity.pid)
        if (
            current is None
            or current.start_time != identity.start_time
            or current.parent_pid != os.getpid()
            or current.state != "Z"
        ):
            continue
        try:
            reaped, _status = os.waitpid(identity.pid, os.WNOHANG)
        except ChildProcessError:
            continue
        except OSError as exc:
            raise NginxGenerationError(
                "identity-bound adopted subprocess could not be reaped"
            ) from exc
        if reaped not in {0, identity.pid}:
            raise NginxGenerationError(
                "identity-bound adopted subprocess reap differed"
            )


def _signal_process_identity(
    identity: ProcessIdentity,
    signum: int,
) -> None:
    current = _process_identity(identity.pid)
    if current is None or current.start_time != identity.start_time:
        return
    try:
        descriptor = os.pidfd_open(identity.pid, 0)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise NginxGenerationError(
            "identity-bound subprocess handle cannot be opened"
        ) from exc
    try:
        refreshed = _process_identity(identity.pid)
        if refreshed is None or refreshed.start_time != identity.start_time:
            return
        signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise NginxGenerationError(
            "identity-bound subprocess signal failed"
        ) from exc
    finally:
        os.close(descriptor)


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    root: ProcessIdentity,
    tracked: set[ProcessIdentity],
    *,
    baseline_children: frozenset[tuple[int, int]],
) -> None:
    def refresh() -> None:
        tracked.update(
            _owned_processes(
                root,
                baseline_children=baseline_children,
            )
        )
        _reap_adopted_zombies(tracked, root_pid=root.pid)

    def signal_live(*, force: bool) -> None:
        refresh()
        for identity in tuple(tracked):
            if _identity_is_live(identity):
                _signal_process_identity(
                    identity,
                    (
                        signal.SIGKILL
                        if force
                        or identity.process_group != root.process_group
                        else signal.SIGTERM
                    ),
                )

    signal_live(force=False)
    deadline = time.monotonic() + COMMAND_TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        process.poll()
        signal_live(force=False)
        if process.poll() is not None and not any(
            _identity_is_live(identity) for identity in tracked
        ):
            break
        time.sleep(
            min(
                PROCESS_POLL_SECONDS,
                max(0.0, deadline - time.monotonic()),
            )
        )
    signal_live(force=True)
    try:
        process.wait(timeout=COMMAND_TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=COMMAND_TERM_GRACE_SECONDS)
    absence_deadline = (
        time.monotonic()
        + COMMAND_TERM_GRACE_SECONDS
        + PROCESS_TREE_QUIESCENCE_SECONDS
    )
    stable_since: float | None = None
    while time.monotonic() < absence_deadline:
        refresh()
        live = {
            identity for identity in tracked if _identity_is_live(identity)
        }
        if live:
            stable_since = None
            for identity in live:
                _signal_process_identity(identity, signal.SIGKILL)
        elif stable_since is None:
            stable_since = time.monotonic()
        elif (
            time.monotonic() - stable_since
            >= PROCESS_TREE_QUIESCENCE_SECONDS
        ):
            return
        time.sleep(
            min(
                PROCESS_POLL_SECONDS,
                max(0.0, absence_deadline - time.monotonic()),
            )
        )
    refresh()
    if any(
        identity.pid != root.pid and _identity_is_current(identity)
        for identity in tracked
    ):
        raise NginxGenerationError(
            "subprocess process tree survived forced cleanup"
        )


def _subprocess_runner(argv: Sequence[str], timeout: int) -> CommandResult:
    if (
        type(timeout) is not int
        or not 1 <= timeout <= MAX_COMMAND_TIMEOUT_SECONDS
    ):
        raise NginxGenerationError(
            "bounded Nginx command timeout is outside its contract"
        )
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    tracked: set[ProcessIdentity] = set()
    root: ProcessIdentity | None = None
    cleaned = False
    deadline = time.monotonic() + timeout
    _enable_child_subreaper()
    baseline_children = _direct_child_baseline()
    try:
        process = subprocess.Popen(  # noqa: S603
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME": "/nonexistent",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
            close_fds=True,
            pass_fds=(),
            start_new_session=True,
        )
        root = _process_identity(process.pid)
        if root is None:
            process.poll()
            root = ProcessIdentity(
                pid=process.pid,
                parent_pid=os.getpid(),
                process_group=process.pid,
                start_time=-1,
                state="?",
            )
        if process.stdout is None or process.stderr is None:
            raise NginxGenerationError(
                "bounded Nginx command pipes are unavailable"
            )
        for label, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            tracked.update(
                _owned_processes(
                    root,
                    baseline_children=baseline_children,
                )
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NginxGenerationError(
                    "bounded Nginx command timed out"
                )
            events = selector.select(
                min(PROCESS_POLL_SECONDS, remaining)
            )
            if not events:
                if process.poll() is not None and not cleaned:
                    _terminate_process_tree(
                        process,
                        root,
                        tracked,
                        baseline_children=baseline_children,
                    )
                    cleaned = True
                continue
            for key, _mask in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                label = key.data
                if (
                    len(buffers[label]) + len(chunk)
                    > MAX_COMMAND_STREAM_BYTES
                ):
                    raise NginxGenerationError(
                        f"bounded Nginx command {label} is oversized"
                    )
                buffers[label].extend(chunk)
            if process.poll() is not None and not cleaned:
                _terminate_process_tree(
                    process,
                    root,
                    tracked,
                    baseline_children=baseline_children,
                )
                cleaned = True
        returncode = process.poll()
        if returncode is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NginxGenerationError(
                    "bounded Nginx command timed out"
                )
            returncode = process.wait(timeout=remaining)
    except NginxGenerationError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise NginxGenerationError(
            "bounded Nginx command could not execute"
        ) from exc
    finally:
        selector.close()
        if process is not None and root is not None:
            try:
                if not cleaned:
                    _terminate_process_tree(
                        process,
                        root,
                        tracked,
                        baseline_children=baseline_children,
                    )
            finally:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
    return CommandResult(
        returncode,
        bytes(buffers["stdout"]),
        bytes(buffers["stderr"]),
    )
def _run_checked(
    argv: Sequence[str],
    *,
    runner: RunFn,
    label: str,
    timeout: int = 30,
) -> dict[str, Any]:
    result = runner(tuple(argv), timeout)
    if (
        not isinstance(result, CommandResult)
        or not isinstance(result.stdout, bytes)
        or not isinstance(result.stderr, bytes)
        or len(result.stdout) > 1024 * 1024
        or len(result.stderr) > 1024 * 1024
    ):
        raise NginxGenerationError(f"{label} runner result is invalid")
    evidence = {
        "argv_sha256": _sha256(canonical_json_bytes(list(argv))),
        "returncode": result.returncode,
        "stdout_sha256": _sha256(result.stdout),
        "stderr_sha256": _sha256(result.stderr),
        "stdout_bytes": len(result.stdout),
        "stderr_bytes": len(result.stderr),
    }
    if result.returncode != 0:
        raise NginxCommandError(f"{label} failed", evidence)
    return evidence


def _atomic_replace_destination(
    path: Path,
    payload: bytes,
    *,
    expected_sha256: set[str],
    owner_uid: int,
) -> None:
    current = _read_stable_regular(
        path,
        label=f"active Nginx destination {path}",
        owner_uid=owner_uid,
    )
    if _sha256(current) not in expected_sha256:
        raise NginxGenerationError(f"active Nginx destination drifted: {path}")
    try:
        metadata = path.stat(follow_symlinks=False)
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        directory_metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != owner_uid
            or stat.S_IMODE(directory_metadata.st_mode) & 0o022
        ):
            os.close(directory_fd)
            raise NginxGenerationError(
                f"active Nginx destination directory is unsafe: {path.parent}"
            )
        temporary = f".{path.name}.production-shadow.{secrets.token_hex(8)}"
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                stat.S_IMODE(metadata.st_mode),
                dir_fd=directory_fd,
            )
            written = 0
            while written < len(payload):
                count = os.write(descriptor, payload[written:])
                if count <= 0:
                    raise NginxGenerationError(
                        "atomic Nginx write made no progress"
                    )
                written += count
            os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
            os.fchown(descriptor, metadata.st_uid, metadata.st_gid)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(
                temporary,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            os.close(directory_fd)
    except NginxGenerationError:
        raise
    except OSError as exc:
        raise NginxGenerationError(
            f"atomic Nginx destination replacement failed: {path}"
        ) from exc


def confirmation_phrase(
    *,
    action: str,
    operation_id: str,
    role: str,
    generation: str | None,
) -> str:
    suffix = f":{generation}" if generation is not None else ""
    return (
        f"APPLY-PRODUCTION-NGINX-{action.upper()}:{operation_id}:{role}{suffix}"
    )


def _install(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    manifest_payload: bytes,
    manifest_sha256: str,
    archive_path: Path,
    members: Mapping[str, bytes],
    layout: HostLayout,
) -> dict[str, Any]:
    _ensure_private_directory(layout.operation_base, owner_uid=layout.owner_uid, create=False)
    operation_directory = root.parent
    if not operation_directory.exists():
        operation_directory.mkdir(mode=0o700)
    _ensure_private_directory(
        operation_directory, owner_uid=layout.owner_uid, create=False
    )
    if not root.exists():
        root.mkdir(mode=0o700)
    _ensure_private_directory(root, owner_uid=layout.owner_uid, create=False)
    archive_payload = _read_stable_regular(
        archive_path,
        label="role generation archive",
        owner_uid=layout.owner_uid,
        maximum=MAX_ARCHIVE_BYTES,
        private=True,
    )
    _write_new_or_verify(
        root / "manifest.json",
        manifest_payload,
        label="installed role generation manifest",
        owner_uid=layout.owner_uid,
        maximum=MAX_JSON_BYTES,
    )
    _write_new_or_verify(
        root / "archive.tar",
        archive_payload,
        label="installed role generation archive",
        owner_uid=layout.owner_uid,
        maximum=MAX_ARCHIVE_BYTES,
    )
    for name, payload in members.items():
        target = _stage_path(root, name)
        current = root
        for part in target.relative_to(root).parts[:-1]:
            current = current / part
            if not current.exists():
                current.mkdir(mode=0o700)
            _ensure_private_directory(current, owner_uid=layout.owner_uid, create=False)
        _write_new_or_verify(
            target,
            payload,
            label=f"installed Nginx generation member {name}",
            owner_uid=layout.owner_uid,
            maximum=MAX_CONFIG_BYTES,
        )
    journal_path = root / "journal.json"
    if journal_path.exists() or journal_path.is_symlink():
        state = _load_journal(
            journal_path,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            owner_uid=layout.owner_uid,
        )
        return _host_action_result(
            manifest,
            manifest_sha256=manifest_sha256,
            action="install",
            generation=None,
            status="already-installed",
            active_configuration_mutated=False,
            service_reloaded=False,
            journal_sha256=state["state_sha256"],
        )
    active = _active_state(
        manifest, members, layout=layout, owner_uid=layout.owner_uid
    )
    if active != "legacy-normal":
        raise NginxGenerationError("initial install requires exact legacy-normal active state")
    state = _new_journal(manifest, manifest_sha256)
    _append_event(state, "installed", active_state=active)
    _write_journal(journal_path, state, create=True)
    return _host_action_result(
        manifest,
        manifest_sha256=manifest_sha256,
        action="install",
        generation=None,
        status="installed",
        active_configuration_mutated=False,
        service_reloaded=False,
        journal_sha256=state["state_sha256"],
    )


def _host_action_result(
    manifest: Mapping[str, Any],
    *,
    manifest_sha256: str,
    action: str,
    generation: str | None,
    status: str,
    active_configuration_mutated: bool,
    service_reloaded: bool,
    journal_sha256: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "schema": HOST_ACTION_RESULT_SCHEMA,
        "status": status,
        "action": action,
        "generation": generation,
        "state": generation,
        "operation_id": manifest["operation_id"],
        "role": manifest["role"],
        "expected_host": manifest["expected_host"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "manifest_sha256": manifest_sha256,
        "archive_sha256": manifest["archive"]["sha256"],
        "active_configuration_mutated": active_configuration_mutated,
        "service_reloaded": service_reloaded,
        "journal_sha256": journal_sha256,
        **details,
    }


def _require_inventory_unchanged(
    expected_sha256: str,
    manifest: Mapping[str, Any],
    *,
    layout: HostLayout,
) -> list[dict[str, Any]]:
    inventory, observed_sha256 = _enabled_inventory(manifest, layout=layout)
    if observed_sha256 != expected_sha256:
        raise NginxGenerationError("enabled-sites inventory drifted after candidate test")
    return inventory


def _test_generation(
    *,
    root: Path,
    state: str,
    manifest: Mapping[str, Any],
    members: Mapping[str, bytes],
    manifest_sha256: str,
    layout: HostLayout,
    runner: RunFn,
) -> dict[str, Any]:
    journal_path = root / "journal.json"
    journal = _load_journal(
        journal_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        owner_uid=layout.owner_uid,
    )
    inventory, inventory_sha256 = _enabled_inventory(manifest, layout=layout)
    candidate, candidate_sha256 = _candidate_files(
        root=root,
        state=state,
        manifest=manifest,
        members=members,
        inventory=inventory,
        layout=layout,
    )
    existing = journal["tested_states"].get(state)
    expected = {
        "inventory_sha256": inventory_sha256,
        "candidate_sha256": candidate_sha256,
    }
    if existing is not None:
        if existing != expected:
            raise NginxGenerationError("completed candidate test binding differs")
        return _host_action_result(
            manifest,
            manifest_sha256=manifest_sha256,
            action="test",
            generation=state,
            status="already-tested",
            active_configuration_mutated=False,
            service_reloaded=False,
            journal_sha256=journal["state_sha256"],
            **expected,
        )
    command = (
        os.fspath(layout.nginx_bin),
        "-t",
        "-c",
        os.fspath(candidate),
        "-p",
        "/",
    )
    try:
        command_evidence = _run_checked(
            command,
            runner=runner,
            label=f"{state} candidate Nginx test",
        )
    except NginxCommandError as exc:
        _append_event(
            journal,
            "test-failed",
            state=state,
            inventory_sha256=inventory_sha256,
            candidate_sha256=candidate_sha256,
            command=exc.evidence,
        )
        _write_journal(journal_path, journal, create=False)
        raise
    journal["tested_states"][state] = expected
    _append_event(
        journal,
        "tested",
        state=state,
        inventory_sha256=inventory_sha256,
        candidate_sha256=candidate_sha256,
        command=command_evidence,
    )
    _write_journal(journal_path, journal, create=False)
    return _host_action_result(
        manifest,
        manifest_sha256=manifest_sha256,
        action="test",
        generation=state,
        status="tested",
        active_configuration_mutated=False,
        service_reloaded=False,
        journal_sha256=journal["state_sha256"],
        command=command_evidence,
        **expected,
    )


def _active_test_and_reload(
    *,
    manifest: Mapping[str, Any],
    members: Mapping[str, bytes],
    expected_state: str,
    layout: HostLayout,
    runner: RunFn,
) -> dict[str, Any]:
    if (
        type(layout.reload_stability_observations) is not int
        or not 2 <= layout.reload_stability_observations <= 10
        or isinstance(layout.reload_stability_interval_seconds, bool)
        or not isinstance(
            layout.reload_stability_interval_seconds,
            (int, float),
        )
        or not 0
        <= layout.reload_stability_interval_seconds
        <= 30
    ):
        raise NginxGenerationError(
            "Nginx reload stability contract is invalid"
        )
    test = _run_checked(
        (
            os.fspath(layout.nginx_bin),
            "-t",
            "-c",
            os.fspath(layout.nginx_conf),
            "-p",
            "/",
        ),
        runner=runner,
        label="active Nginx test",
    )
    if tuple(layout.reload_argv) != DEFAULT_RELOAD_ARGV:
        # Injectable layouts may relocate binaries in tests, but reload remains
        # an explicit fixed argv supplied as an immutable tuple.
        if (
            len(layout.reload_argv) != 3
            or layout.reload_argv[1:] != ("reload", "nginx")
            or not Path(layout.reload_argv[0]).is_absolute()
        ):
            raise NginxGenerationError("Nginx reload argv is not bounded")
    reload_result = _run_checked(
        layout.reload_argv,
        runner=runner,
        label="Nginx reload",
    )
    service_status_argv = (
        layout.reload_argv[0],
        "is-active",
        "--quiet",
        "nginx",
    )
    stability: list[dict[str, Any]] = []
    started = time.monotonic()
    for index in range(1, layout.reload_stability_observations + 1):
        target = (
            started + index * layout.reload_stability_interval_seconds
        )
        remaining = target - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        service = _run_checked(
            service_status_argv,
            runner=runner,
            label=f"Nginx service stability observation {index}",
        )
        nginx_test = _run_checked(
            (
                os.fspath(layout.nginx_bin),
                "-t",
                "-c",
                os.fspath(layout.nginx_conf),
                "-p",
                "/",
            ),
            runner=runner,
            label=f"Nginx config stability observation {index}",
        )
        readback = _active_state(
            manifest,
            members,
            layout=layout,
            owner_uid=layout.owner_uid,
        )
        if readback != expected_state:
            raise NginxGenerationError(
                "Nginx reload stability readback differs"
            )
        stability.append(
            {
                "index": index,
                "service": service,
                "nginx_test": nginx_test,
                "state": readback,
            }
        )
    return {
        "test": test,
        "reload": reload_result,
        "stability": stability,
    }


def _rollback_transaction(
    *,
    journal: dict[str, Any],
    journal_path: Path,
    manifest: Mapping[str, Any],
    members: Mapping[str, bytes],
    layout: HostLayout,
    runner: RunFn,
    reason: str,
    failure_evidence: Mapping[str, Any] | None,
) -> None:
    transaction = journal["transaction"]
    if not isinstance(transaction, dict):
        raise NginxGenerationError("rollback transaction state is absent")
    from_state = transaction["from_state"]
    to_state = transaction["to_state"]
    previous = _destination_payloads(
        manifest, members, from_state, layout=layout
    )
    target = _destination_payloads(
        manifest, members, to_state, layout=layout
    )
    transaction["status"] = "rolling-back"
    transaction["rollback_reason"] = reason
    transaction["failure_evidence"] = (
        dict(failure_evidence) if failure_evidence is not None else None
    )
    _write_journal(journal_path, journal, create=False)
    try:
        for path in sorted(previous, key=os.fspath):
            _atomic_replace_destination(
                path,
                previous[path],
                expected_sha256={
                    _sha256(previous[path]),
                    _sha256(target[path]),
                },
                owner_uid=layout.owner_uid,
            )
        transaction["status"] = "rollback-validating"
        _write_journal(journal_path, journal, create=False)
        commands = _active_test_and_reload(
            manifest=manifest,
            members=members,
            expected_state=from_state,
            layout=layout,
            runner=runner,
        )
        observed = _active_state(
            manifest, members, layout=layout, owner_uid=layout.owner_uid
        )
        if observed != from_state:
            raise NginxGenerationError("rollback readback differs from previous generation")
    except BaseException as exc:
        transaction["status"] = "rollback-failed"
        transaction["rollback_failure_evidence"] = (
            exc.evidence if isinstance(exc, NginxCommandError) else None
        )
        _write_journal(journal_path, journal, create=False)
        raise
    transaction["status"] = "rolled-back"
    transaction["rollback_commands"] = commands
    journal["active_state"] = from_state
    _append_event(
        journal,
        "rolled-back",
        from_state=from_state,
        failed_target=to_state,
        reason=reason,
        generation_sha256=manifest["generation_sha256"][from_state],
        commands=commands,
        failure_evidence=transaction["failure_evidence"],
    )
    journal["transaction"] = None
    _write_journal(journal_path, journal, create=False)


def _recover_transaction(
    *,
    journal: dict[str, Any],
    journal_path: Path,
    manifest: Mapping[str, Any],
    members: Mapping[str, bytes],
    layout: HostLayout,
    runner: RunFn,
) -> dict[str, Any]:
    transaction = journal["transaction"]
    if transaction is None:
        return journal
    if transaction.get("status") == "rollback-failed":
        raise NginxGenerationError(
            "prior Nginx rollback failed and needs operator inspection"
        )
    _rollback_transaction(
        journal=journal,
        journal_path=journal_path,
        manifest=manifest,
        members=members,
        layout=layout,
        runner=runner,
        reason="crash-resume-recovery",
        failure_evidence=None,
    )
    return _load_journal(
        journal_path,
        manifest=manifest,
        manifest_sha256=journal["manifest_sha256"],
        owner_uid=layout.owner_uid,
    )


def _activate(
    *,
    root: Path,
    target_state: str,
    restore: bool,
    rollback_freeze: bool,
    manifest: Mapping[str, Any],
    members: Mapping[str, bytes],
    manifest_sha256: str,
    layout: HostLayout,
    runner: RunFn,
) -> dict[str, Any]:
    journal_path = root / "journal.json"
    journal = _load_journal(
        journal_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        owner_uid=layout.owner_uid,
    )
    journal = _recover_transaction(
        journal=journal,
        journal_path=journal_path,
        manifest=manifest,
        members=members,
        layout=layout,
        runner=runner,
    )
    observed = _active_state(
        manifest, members, layout=layout, owner_uid=layout.owner_uid
    )
    if observed != journal["active_state"]:
        raise NginxGenerationError("active generation differs from durable journal")
    action = (
        "rollback-freeze"
        if rollback_freeze
        else ("restore" if restore else "activate")
    )
    if observed == target_state:
        return _host_action_result(
            manifest,
            manifest_sha256=manifest_sha256,
            action=action,
            generation=target_state,
            status="already-active",
            active_configuration_mutated=False,
            service_reloaded=False,
            journal_sha256=journal["state_sha256"],
        )
    if rollback_freeze:
        if (
            restore
            or target_state != "legacy-frozen"
            or observed != "shadow-readonly"
        ):
            raise NginxGenerationError(
                "rollback-freeze is allowed only from shadow-readonly"
            )
    elif restore:
        if target_state != "legacy-normal" or observed != "legacy-frozen":
            raise NginxGenerationError(
                "restore is allowed only before shadow-writable and "
                "only from legacy-frozen"
            )
    else:
        allowed = {
            ("legacy-normal", "legacy-frozen"),
            ("legacy-frozen", "shadow-readonly"),
            ("shadow-readonly", "shadow-writable"),
            ("shadow-writable", "shadow-readonly"),
        }
        if (observed, target_state) not in allowed:
            raise NginxGenerationError("requested Nginx generation transition is not allowlisted")
    tested = journal["tested_states"].get(target_state)
    if tested is None:
        raise NginxGenerationError("target Nginx generation has not passed candidate test")
    _require_inventory_unchanged(
        tested["inventory_sha256"],
        manifest,
        layout=layout,
    )
    journal["transaction"] = {
        "from_state": observed,
        "to_state": target_state,
        "status": "prepared",
        "inventory_sha256": tested["inventory_sha256"],
    }
    _append_event(
        journal,
        "transaction-prepared",
        from_state=observed,
        to_state=target_state,
    )
    _write_journal(journal_path, journal, create=False)
    previous = _destination_payloads(
        manifest, members, observed, layout=layout
    )
    target = _destination_payloads(
        manifest, members, target_state, layout=layout
    )
    try:
        journal["transaction"]["status"] = "applying"
        _write_journal(journal_path, journal, create=False)
        for path in sorted(target, key=os.fspath):
            _atomic_replace_destination(
                path,
                target[path],
                expected_sha256={_sha256(previous[path]), _sha256(target[path])},
                owner_uid=layout.owner_uid,
            )
        journal["transaction"]["status"] = "validating"
        _write_journal(journal_path, journal, create=False)
        commands = _active_test_and_reload(
            manifest=manifest,
            members=members,
            expected_state=target_state,
            layout=layout,
            runner=runner,
        )
        readback = _active_state(
            manifest, members, layout=layout, owner_uid=layout.owner_uid
        )
        if readback != target_state:
            raise NginxGenerationError("activated Nginx generation readback differs")
    except BaseException as exc:
        try:
            _rollback_transaction(
                journal=journal,
                journal_path=journal_path,
                manifest=manifest,
                members=members,
                layout=layout,
                runner=runner,
                reason=(
                    f"{type(exc).__name__}: {exc}"
                    if str(exc)
                    else type(exc).__name__
                ),
                failure_evidence=(
                    exc.evidence if isinstance(exc, NginxCommandError) else None
                ),
            )
        except BaseException as rollback_exc:
            raise NginxGenerationError(
                "Nginx activation failed and rollback validation also failed"
            ) from rollback_exc
        if (
            isinstance(exc, NginxGenerationCancellation)
            or not isinstance(exc, Exception)
        ):
            raise
        raise NginxGenerationError(
            "Nginx activation failed; previous generation was restored"
        ) from exc
    journal["active_state"] = target_state
    journal["transaction"]["status"] = "completed"
    journal["transaction"]["commands"] = commands
    _append_event(
        journal,
        "activated",
        from_state=observed,
        to_state=target_state,
        generation_sha256=manifest["generation_sha256"][target_state],
        commands=commands,
    )
    journal["transaction"] = None
    _write_journal(journal_path, journal, create=False)
    return _host_action_result(
        manifest,
        manifest_sha256=manifest_sha256,
        action=action,
        generation=target_state,
        status="activated",
        active_configuration_mutated=True,
        service_reloaded=True,
        journal_sha256=journal["state_sha256"],
        from_state=observed,
        commands=commands,
    )


def _execute_host_action_under_contract(
    *,
    manifest_path: Path,
    expected_manifest_sha256: str,
    archive_path: Path,
    role: str,
    expected_host: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    action: str,
    generation: str | None = None,
    apply: bool = False,
    confirm: str | None = None,
    readback_challenge_nonce: str | None = None,
    readback_challenge_sha256: str | None = None,
    issued_at_epoch: int | None = None,
    expires_at_epoch: int | None = None,
    layout: HostLayout = HostLayout(),
    runner: RunFn = _subprocess_runner,
) -> dict[str, Any]:
    if role not in ROLES or action not in {
        "plan",
        "install",
        "test",
        "activate",
        "rollback-freeze",
        "readback",
        "restore",
    }:
        raise NginxGenerationError("host action or role is not allowlisted")
    if layout.owner_uid != 0 or os.geteuid() != 0:
        raise NginxGenerationError("Nginx host worker requires root ownership")
    addresses = (
        set(layout.identity_addresses)
        if layout.identity_addresses is not None
        else _local_ipv4_addresses()
    )
    if expected_host not in addresses:
        raise NginxGenerationError("local host identity differs from expected host")
    if generation is not None and generation not in GENERATION_STATES:
        raise NginxGenerationError("host generation state is not allowlisted")
    if action in {"test", "activate", "rollback-freeze"} and generation is None:
        raise NginxGenerationError(
            "test, activate, and rollback-freeze require an exact generation"
        )
    if action == "rollback-freeze" and generation != "legacy-frozen":
        raise NginxGenerationError(
            "rollback-freeze target is always legacy-frozen"
        )
    if action == "restore" and generation not in {None, "legacy-normal"}:
        raise NginxGenerationError("restore target is always legacy-normal")
    if action in {"plan", "install", "readback"} and generation is not None:
        raise NginxGenerationError(f"{action} does not accept a generation")
    manifest, manifest_payload, members = load_role_material(
        manifest_path=manifest_path,
        expected_manifest_sha256=expected_manifest_sha256,
        archive_path=archive_path,
        expected_role=role,
        expected_host=expected_host,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        owner_uid=layout.owner_uid,
    )
    root = _operation_root(layout, operation_id, role)
    effective_generation = "legacy-normal" if action == "restore" else generation
    mutating_action = action in {
        "install",
        "test",
        "activate",
        "rollback-freeze",
        "restore",
    }
    required = (
        confirmation_phrase(
            action=action,
            operation_id=operation_id,
            role=role,
            generation=effective_generation,
        )
        if mutating_action
        else None
    )
    if not apply:
        if confirm is not None:
            raise NginxGenerationError("--confirm is valid only with --apply")
        return {
            "schema": "production-shadow-nginx-host-plan-v1",
            "status": "planned",
            "action": action,
            "generation": effective_generation,
            "operation_id": operation_id,
            "role": role,
            "expected_host": expected_host,
            "release_sha": release_sha,
            "release_tree_sha": release_tree_sha,
            "manifest_sha256": expected_manifest_sha256,
            "archive_sha256": manifest["archive"]["sha256"],
            "required_confirmation": required,
            "active_configuration_mutated": False,
            "service_reloaded": False,
        }
    if action == "plan":
        raise NginxGenerationError("plan cannot be executed with --apply")
    if mutating_action and confirm != required:
        raise NginxGenerationError("exact host action confirmation is required")
    if not mutating_action and confirm is not None:
        raise NginxGenerationError("readback does not accept a confirmation")
    if action != "readback" and any(
        value is not None
        for value in (
            readback_challenge_nonce,
            readback_challenge_sha256,
            issued_at_epoch,
            expires_at_epoch,
        )
    ):
        raise NginxGenerationError(
            "fresh readback challenge is valid only for readback"
        )

    if action == "install":
        _ensure_private_directory(
            layout.operation_base,
            owner_uid=layout.owner_uid,
            create=False,
        )
        _ensure_private_directory(
            root.parent,
            owner_uid=layout.owner_uid,
            create=True,
        )
        _ensure_private_directory(
            root,
            owner_uid=layout.owner_uid,
            create=True,
        )
        with _journal_lock(root, owner_uid=layout.owner_uid):
            return _install(
                root=root,
                manifest=manifest,
                manifest_payload=manifest_payload,
                manifest_sha256=expected_manifest_sha256,
                archive_path=archive_path,
                members=members,
                layout=layout,
            )
    if not root.exists() or root.is_symlink():
        raise NginxGenerationError("role generation material is not installed")
    _ensure_private_directory(root, owner_uid=layout.owner_uid, create=False)
    with _journal_lock(root, owner_uid=layout.owner_uid):
        if action == "test":
            return _test_generation(
                root=root,
                state=str(generation),
                manifest=manifest,
                members=members,
                manifest_sha256=expected_manifest_sha256,
                layout=layout,
                runner=runner,
            )
        if action == "activate":
            return _activate(
                root=root,
                target_state=str(generation),
                restore=False,
                rollback_freeze=False,
                manifest=manifest,
                members=members,
                manifest_sha256=expected_manifest_sha256,
                layout=layout,
                runner=runner,
            )
        if action == "rollback-freeze":
            return _activate(
                root=root,
                target_state="legacy-frozen",
                restore=False,
                rollback_freeze=True,
                manifest=manifest,
                members=members,
                manifest_sha256=expected_manifest_sha256,
                layout=layout,
                runner=runner,
            )
        if action == "restore":
            return _activate(
                root=root,
                target_state="legacy-normal",
                restore=True,
                rollback_freeze=False,
                manifest=manifest,
                members=members,
                manifest_sha256=expected_manifest_sha256,
                layout=layout,
                runner=runner,
            )
        _readback_challenge(
            operation_id=operation_id,
            role=role,
            expected_host=expected_host,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            manifest_sha256=expected_manifest_sha256,
            archive_sha256=manifest["archive"]["sha256"],
            challenge_nonce=readback_challenge_nonce,
            challenge_sha256=readback_challenge_sha256,
            issued_at_epoch=issued_at_epoch,
            expires_at_epoch=expires_at_epoch,
            observed_at_epoch=int(time.time()),
        )
        journal = _load_journal(
            root / "journal.json",
            manifest=manifest,
            manifest_sha256=expected_manifest_sha256,
            owner_uid=layout.owner_uid,
        )
        if journal["transaction"] is not None:
            raise NginxGenerationError(
                "readback found a pending transaction; explicit activate or restore "
                "is required for recovery"
            )
        observed = _active_state(
            manifest, members, layout=layout, owner_uid=layout.owner_uid
        )
        if observed != journal["active_state"]:
            raise NginxGenerationError("active Nginx readback differs from journal")
        inventory, inventory_sha256 = _enabled_inventory(manifest, layout=layout)
        captured_at_epoch = int(time.time())
        _readback_challenge(
            operation_id=operation_id,
            role=role,
            expected_host=expected_host,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            manifest_sha256=expected_manifest_sha256,
            archive_sha256=manifest["archive"]["sha256"],
            challenge_nonce=readback_challenge_nonce,
            challenge_sha256=readback_challenge_sha256,
            issued_at_epoch=issued_at_epoch,
            expires_at_epoch=expires_at_epoch,
            observed_at_epoch=captured_at_epoch,
        )
        return {
            "schema": HOST_FRESH_READBACK_SCHEMA,
            "status": "read-back",
            "operation_id": operation_id,
            "role": role,
            "expected_host": expected_host,
            "release_sha": release_sha,
            "release_tree_sha": release_tree_sha,
            "manifest_sha256": expected_manifest_sha256,
            "archive_sha256": manifest["archive"]["sha256"],
            "state": observed,
            "generation_sha256": manifest["generation_sha256"][observed],
            "enabled_inventory_sha256": inventory_sha256,
            "enabled_inventory_count": len(inventory),
            "active_configuration_mutated": False,
            "service_reloaded": False,
            "journal_sha256": journal["state_sha256"],
            "readback_challenge_nonce": readback_challenge_nonce,
            "readback_challenge_sha256": readback_challenge_sha256,
            "issued_at_epoch": issued_at_epoch,
            "expires_at_epoch": expires_at_epoch,
            "captured_at_epoch": captured_at_epoch,
        }


def execute_host_action(
    *,
    manifest_path: Path,
    expected_manifest_sha256: str,
    archive_path: Path,
    role: str,
    expected_host: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    action: str,
    generation: str | None = None,
    apply: bool = False,
    confirm: str | None = None,
    readback_challenge_nonce: str | None = None,
    readback_challenge_sha256: str | None = None,
    issued_at_epoch: int | None = None,
    expires_at_epoch: int | None = None,
    control_fd: int | None = None,
    layout: HostLayout = HostLayout(),
    runner: RunFn = _subprocess_runner,
) -> dict[str, Any]:
    arguments = {
        "manifest_path": manifest_path,
        "expected_manifest_sha256": expected_manifest_sha256,
        "archive_path": archive_path,
        "role": role,
        "expected_host": expected_host,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "action": action,
        "generation": generation,
        "apply": apply,
        "confirm": confirm,
        "readback_challenge_nonce": readback_challenge_nonce,
        "readback_challenge_sha256": readback_challenge_sha256,
        "issued_at_epoch": issued_at_epoch,
        "expires_at_epoch": expires_at_epoch,
        "layout": layout,
        "runner": runner,
    }
    if apply and action in CONTROLLED_HOST_ACTIONS:
        if layout.owner_uid != 0 or os.geteuid() != 0:
            raise NginxGenerationError(
                "controlled Nginx host action requires root ownership"
            )
        if threading.current_thread() is not threading.main_thread():
            raise NginxGenerationError(
                "controlled Nginx host action must run in the main thread"
            )
        if control_fd is None:
            raise NginxGenerationError(
                "controlled Nginx host action requires controller liveness"
            )
        with ControllerLivenessGuard(control_fd):
            return _execute_host_action_under_contract(**arguments)
    if control_fd is not None:
        raise NginxGenerationError(
            "non-mutating Nginx host action rejects controller liveness"
        )
    return _execute_host_action_under_contract(**arguments)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    produce = subparsers.add_parser("produce")
    produce.add_argument("--operation-id", required=True)
    produce.add_argument("--release-sha", required=True)
    produce.add_argument("--release-tree-sha", required=True)
    produce.add_argument("--shadow-release-root", type=Path, required=True)
    produce.add_argument("--bot-fi-shadow-api-port", type=int, required=True)
    produce.add_argument("--webapp-fi-shadow-api-port", type=int, required=True)
    produce.add_argument("--bot-coin-source", type=Path, required=True)
    produce.add_argument("--bot-mini-source", type=Path, required=True)
    produce.add_argument("--bot-mini-legacy-root", required=True)
    produce.add_argument("--webapp-source", type=Path, required=True)
    produce.add_argument("--output-root", type=Path, required=True)

    host = subparsers.add_parser("host")
    host.add_argument("--manifest", type=Path, required=True)
    host.add_argument("--manifest-sha256", required=True)
    host.add_argument("--archive", type=Path, required=True)
    host.add_argument("--role", choices=ROLES, required=True)
    host.add_argument("--expected-host", required=True)
    host.add_argument("--operation-id", required=True)
    host.add_argument("--release-sha", required=True)
    host.add_argument("--release-tree-sha", required=True)
    host.add_argument(
        "--action",
        choices=(
            "plan",
            "install",
            "test",
            "activate",
            "rollback-freeze",
            "readback",
            "restore",
        ),
        default="plan",
    )
    host.add_argument("--generation", choices=GENERATION_STATES)
    host.add_argument("--apply", action="store_true")
    host.add_argument("--confirm")
    host.add_argument("--readback-challenge-nonce")
    host.add_argument("--readback-challenge-sha256")
    host.add_argument("--issued-at-epoch", type=int)
    host.add_argument("--expires-at-epoch", type=int)
    host.add_argument("--control-fd", type=int, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
        if os.geteuid() != 0:
            raise NginxGenerationError("production Nginx generation tool must run as root")
        if args.command == "produce":
            result = produce_generations(
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                release_tree_sha=args.release_tree_sha,
                shadow_release_root=args.shadow_release_root,
                role_api_ports={
                    "bot_fi": args.bot_fi_shadow_api_port,
                    "webapp_fi": args.webapp_fi_shadow_api_port,
                },
                sources=default_sources(
                    bot_coin_source=args.bot_coin_source,
                    bot_mini_source=args.bot_mini_source,
                    bot_mini_legacy_root=args.bot_mini_legacy_root,
                    webapp_source=args.webapp_source,
                ),
                output_root=args.output_root,
            )
        else:
            result = execute_host_action(
                manifest_path=args.manifest,
                expected_manifest_sha256=args.manifest_sha256,
                archive_path=args.archive,
                role=args.role,
                expected_host=args.expected_host,
                operation_id=args.operation_id,
                release_sha=args.release_sha,
                release_tree_sha=args.release_tree_sha,
                action=args.action,
                generation=args.generation,
                apply=args.apply,
                confirm=args.confirm,
                readback_challenge_nonce=args.readback_challenge_nonce,
                readback_challenge_sha256=args.readback_challenge_sha256,
                issued_at_epoch=args.issued_at_epoch,
                expires_at_epoch=args.expires_at_epoch,
                control_fd=args.control_fd,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (NginxGenerationError, OSError) as exc:
        error_document = {
            "status": "blocked",
            "error": str(exc),
            "error_class": type(exc).__name__,
        }
        if isinstance(exc, NginxCommandError):
            error_document["command_evidence"] = exc.evidence
        print(
            json.dumps(
                error_document,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
