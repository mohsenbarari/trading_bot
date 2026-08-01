"""Fail-closed local ``age`` adapters for the physical three-site data plane.

The physical WAL, base-backup, blob, and manifest transport contracts all
deliberately accept injected age adapters.  This module is the narrow runtime
implementation for that seam.  It is intentionally *not* an Object Storage
client, a key distributor, a PostgreSQL tool, or a promotion mechanism.

Two direction-bound adapters are provided:

* :class:`PhysicalAgeV1Encryptor` accepts only its one root-configured public
  recipient.  It snapshots the caller's private input in a private workspace,
  invokes the fixed ``/usr/bin/age`` without a shell, validates the age-v1
  result, and creates the requested destination exactly once.
* :class:`PhysicalAgeV1Decryptor` first derives the public recipient from its
  root-private identity with the fixed ``/usr/bin/age-keygen -y``.  It rejects
  a caller's expected recipient unless it is that exact derived recipient,
  then performs the same private-workspace and no-overwrite discipline.

Importing this module performs no filesystem, subprocess, secret, network,
database, Object Storage, direct-site, or promotion action.  Both adapters
are default-disabled.  Deployment must supply separately root-owned,
mode-0700 workspaces and a root-private identity only on a receiver.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import resource
import stat
import subprocess
import tempfile

from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.append_only_sync_delta_batch import OBJECT_KEY_RE, VERSION_ID_RE
from core.physical_wal_receiver_staging import PhysicalWalDecryptionReadback


__all__ = (
    "DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES",
    "DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES",
    "FIXED_AGE_BINARY",
    "FIXED_AGE_KEYGEN_BINARY",
    "PHYSICAL_AGE_V1_DEFAULT_ENABLED",
    "PhysicalAgeV1AdapterError",
    "PhysicalAgeV1Decryptor",
    "PhysicalAgeV1DecryptorConfig",
    "PhysicalAgeV1FdDecryptor",
    "PhysicalAgeV1Encryptor",
    "PhysicalAgeV1EncryptorConfig",
)


FIXED_AGE_BINARY = Path("/usr/bin/age")
FIXED_AGE_KEYGEN_BINARY = Path("/usr/bin/age-keygen")
PHYSICAL_AGE_V1_DEFAULT_ENABLED = False

# The physical data-plane contracts impose their own tighter object-specific
# caps.  These defaults are deliberately only a broad final hard ceiling so a
# mistakenly unconstrained adapter cannot turn a receiver into an unbounded
# local writer.  They cover the largest currently admissible physical blob.
DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES = 512 * 1024 * 1024 * 1024
DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES = (
    DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES + 64 * 1024 * 1024
)
MAX_PHYSICAL_AGE_IDENTITY_BYTES = 64 * 1024
_AGE_HEADER = b"age-encryption.org/v1\n"
_COPY_CHUNK_BYTES = 1024 * 1024
_MUTABLE_OBJECT_COMPONENTS = frozenset({"alias", "current", "head", "latest", "pointer"})
_MUTABLE_VERSION_IDS = frozenset({"alias", "current", "head", "latest", "pointer"})


class PhysicalAgeV1AdapterError(ValueError):
    """A local age invocation would violate the physical transport contract."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalAgeV1EncryptorConfig:
    """Root-only, one-recipient configuration for an outbound physical route."""

    workspace_root: Path | None = None
    recipient: str = ""
    enabled: bool = PHYSICAL_AGE_V1_DEFAULT_ENABLED
    maximum_plaintext_bytes: int = DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES
    maximum_ciphertext_bytes: int = DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalAgeV1DecryptorConfig:
    """Root-only receiver configuration with one identity and public pin."""

    workspace_root: Path | None = None
    identity_path: Path | None = None
    recipient: str = ""
    enabled: bool = PHYSICAL_AGE_V1_DEFAULT_ENABLED
    maximum_plaintext_bytes: int = DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES
    maximum_ciphertext_bytes: int = DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class _EncryptorFacts:
    workspace_root: Path
    recipient: str
    maximum_plaintext_bytes: int
    maximum_ciphertext_bytes: int


@dataclass(frozen=True)
class _DecryptorFacts:
    workspace_root: Path
    identity_path: Path
    recipient: str
    maximum_plaintext_bytes: int
    maximum_ciphertext_bytes: int


def _fail(code: str) -> None:
    raise PhysicalAgeV1AdapterError(code)


def _require_safe_absolute_path(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    return value


def _require_private_directory(value: object, *, code: str) -> Path:
    path = _require_safe_absolute_path(value, code=code)
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _require_root_controlled_executable(path: Path, *, code: str) -> Path:
    """Accept only the pinned executable at its exact canonical location."""

    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
        target = os.lstat(resolved)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISREG(target.st_mode)
        or target.st_uid != 0
        or target.st_nlink != 1
        or stat.S_IMODE(target.st_mode) & 0o022
        or not (target.st_mode & stat.S_IXUSR)
    ):
        _fail(code)
    return resolved


def _require_recipient(value: object, *, code: str) -> str:
    if not isinstance(value, str) or not AGE_RECIPIENT_RE.fullmatch(value):
        _fail(code)
    return value


def _require_positive_bound(value: object, *, code: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        _fail(code)
    return value


def _normalise_encryptor_config(value: object) -> _EncryptorFacts:
    if type(value) is not PhysicalAgeV1EncryptorConfig:
        _fail("AGE_ENCRYPTOR_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("AGE_ENCRYPTOR_DISABLED")
    if value.direct_site_control != "forbidden" or value.destination_object_ingest != "pull-only":
        _fail("AGE_ENCRYPTOR_ROUTE_POLICY_INVALID")
    maximum_plaintext = _require_positive_bound(
        value.maximum_plaintext_bytes,
        code="AGE_ENCRYPTOR_PLAINTEXT_BOUND_INVALID",
        maximum=DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES,
    )
    maximum_ciphertext = _require_positive_bound(
        value.maximum_ciphertext_bytes,
        code="AGE_ENCRYPTOR_CIPHERTEXT_BOUND_INVALID",
        maximum=DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES,
    )
    if maximum_ciphertext < maximum_plaintext:
        _fail("AGE_ENCRYPTOR_CIPHERTEXT_BOUND_INVALID")
    return _EncryptorFacts(
        workspace_root=_require_private_directory(
            value.workspace_root,
            code="AGE_ENCRYPTOR_WORKSPACE_UNSAFE",
        ),
        recipient=_require_recipient(value.recipient, code="AGE_ENCRYPTOR_RECIPIENT_INVALID"),
        maximum_plaintext_bytes=maximum_plaintext,
        maximum_ciphertext_bytes=maximum_ciphertext,
    )


def _normalise_decryptor_config(value: object) -> _DecryptorFacts:
    if type(value) is not PhysicalAgeV1DecryptorConfig:
        _fail("AGE_DECRYPTOR_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("AGE_DECRYPTOR_DISABLED")
    if value.direct_site_control != "forbidden" or value.destination_object_ingest != "pull-only":
        _fail("AGE_DECRYPTOR_ROUTE_POLICY_INVALID")
    maximum_plaintext = _require_positive_bound(
        value.maximum_plaintext_bytes,
        code="AGE_DECRYPTOR_PLAINTEXT_BOUND_INVALID",
        maximum=DEFAULT_MAX_PHYSICAL_AGE_PLAINTEXT_BYTES,
    )
    maximum_ciphertext = _require_positive_bound(
        value.maximum_ciphertext_bytes,
        code="AGE_DECRYPTOR_CIPHERTEXT_BOUND_INVALID",
        maximum=DEFAULT_MAX_PHYSICAL_AGE_CIPHERTEXT_BYTES,
    )
    if maximum_ciphertext < maximum_plaintext:
        _fail("AGE_DECRYPTOR_CIPHERTEXT_BOUND_INVALID")
    identity = _require_private_input_file(
        value.identity_path,
        code="AGE_DECRYPTOR_IDENTITY_UNSAFE",
        maximum_bytes=MAX_PHYSICAL_AGE_IDENTITY_BYTES,
        allow_modes=frozenset({0o400, 0o600}),
        minimum_bytes=1,
    )
    return _DecryptorFacts(
        workspace_root=_require_private_directory(
            value.workspace_root,
            code="AGE_DECRYPTOR_WORKSPACE_UNSAFE",
        ),
        identity_path=identity,
        recipient=_require_recipient(value.recipient, code="AGE_DECRYPTOR_RECIPIENT_INVALID"),
        maximum_plaintext_bytes=maximum_plaintext,
        maximum_ciphertext_bytes=maximum_ciphertext,
    )


def _require_private_input_file(
    value: object,
    *,
    code: str,
    maximum_bytes: int,
    allow_modes: frozenset[int],
    minimum_bytes: int,
) -> Path:
    path = _require_safe_absolute_path(value, code=code)
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("AGE_PLATFORM_NO_NOFOLLOW")
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail(code)
    try:
        opened = os.fstat(descriptor)
    except OSError:
        os.close(descriptor)
        _fail(code)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    mode = stat.S_IMODE(opened.st_mode)
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or mode not in allow_modes
        or opened.st_dev != before.st_dev
        or opened.st_ino != before.st_ino
        or opened.st_size != before.st_size
        or opened.st_size < minimum_bytes
        or opened.st_size > maximum_bytes
    ):
        _fail(code)
    return path


def _require_new_private_destination(value: object, *, code: str) -> Path:
    path = _require_safe_absolute_path(value, code=code)
    if path.name in {"", ".", ".."}:
        _fail(code)
    parent = _require_private_directory(path.parent, code=code)
    destination = parent / path.name
    try:
        # ``lexists`` also catches a dangling symlink; a normal ``exists``
        # check alone would not.
        exists = os.path.lexists(destination)
    except OSError:
        _fail(code)
    if exists:
        _fail(code)
    return destination


def _opened_file_matches(
    descriptor: int,
    *,
    expected: os.stat_result,
    allowed_modes: frozenset[int],
    minimum_bytes: int,
    maximum_bytes: int,
) -> bool:
    try:
        metadata = os.fstat(descriptor)
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) in allowed_modes
        and metadata.st_dev == expected.st_dev
        and metadata.st_ino == expected.st_ino
        and metadata.st_size == expected.st_size
        and minimum_bytes <= metadata.st_size <= maximum_bytes
    )


def _remove_our_new_file(path: Path) -> None:
    """Best-effort cleanup only for a private regular file we just created."""

    try:
        metadata = os.lstat(path)
        if (
            not stat.S_ISLNK(metadata.st_mode)
            and stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and metadata.st_nlink == 1
        ):
            os.unlink(path)
    except OSError:
        pass


def _copy_private_file_to_new(
    source: Path,
    destination: Path,
    *,
    maximum_bytes: int,
    source_modes: frozenset[int],
    code: str,
) -> tuple[int, int]:
    """FD-copy a stable private source into one new mode-0600 destination."""

    if not hasattr(os, "O_NOFOLLOW"):
        _fail("AGE_PLATFORM_NO_NOFOLLOW")
    source_fd: int | None = None
    try:
        source_before = os.lstat(source)
        source_fd = os.open(
            source,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        _fail(code)
    destination_fd: int | None = None
    created = False
    try:
        if not _opened_file_matches(
            source_fd,
            expected=source_before,
            allowed_modes=source_modes,
            minimum_bytes=1,
            maximum_bytes=maximum_bytes,
        ):
            _fail(code)
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        created = True
        os.fchmod(destination_fd, 0o600)
        total = 0
        while True:
            chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                _fail(code)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if not isinstance(written, int) or written <= 0:
                    _fail(code)
                view = view[written:]
        source_after = os.fstat(source_fd)
        source_path_after = os.lstat(source)
        if (
            total != source_before.st_size
            or source_after.st_dev != source_before.st_dev
            or source_after.st_ino != source_before.st_ino
            or source_after.st_size != source_before.st_size
            or source_path_after.st_dev != source_before.st_dev
            or source_path_after.st_ino != source_before.st_ino
            or source_path_after.st_size != source_before.st_size
        ):
            _fail(code)
        os.fsync(destination_fd)
        output = os.fstat(destination_fd)
        if (
            not stat.S_ISREG(output.st_mode)
            or output.st_uid != os.geteuid()
            or output.st_nlink != 1
            or stat.S_IMODE(output.st_mode) != 0o600
            or output.st_size != total
            or total < 1
        ):
            _fail(code)
        return total, output.st_ino
    except OSError:
        _fail(code)
    finally:
        if source_fd is not None:
            try:
                os.close(source_fd)
            except OSError:
                pass
        if destination_fd is not None:
            try:
                os.close(destination_fd)
            except OSError:
                pass
        if created:
            # A successful caller needs the destination, so this removal is
            # performed only by the exception path through the surrounding
            # helper.  The ``finally`` remains intentionally empty here.
            pass


def _verify_private_output(
    path: Path,
    *,
    code: str,
    minimum_bytes: int,
    maximum_bytes: int,
    require_age_header: bool,
) -> None:
    _require_private_input_file(
        path,
        code=code,
        maximum_bytes=maximum_bytes,
        allow_modes=frozenset({0o600}),
        minimum_bytes=minimum_bytes,
    )
    if not require_age_header:
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail(code)
    try:
        if os.read(descriptor, len(_AGE_HEADER)) != _AGE_HEADER:
            _fail(code)
    except OSError:
        _fail(code)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _private_temporary_workspace(root: Path, *, prefix: str, code: str):
    try:
        temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=str(root))
    except OSError:
        _fail(code)
    path = Path(temporary.name)
    try:
        os.chmod(path, 0o700)
        _require_private_directory(path, code=code)
    except Exception:
        temporary.cleanup()
        raise
    return temporary, path


def _child_preexec(maximum_output_bytes: int) -> None:
    """Set a strict umask and output-file ceiling in the short-lived child."""

    os.umask(0o077)
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (maximum_output_bytes, maximum_output_bytes))
    except (ValueError, OSError):
        # Failure to install the bound makes execution fail closed rather than
        # allowing a ciphertext/plaintext expansion outside the contract.
        os._exit(126)


def _run_age_transform(
    command: list[str],
    *,
    maximum_output_bytes: int,
    timeout_seconds: int,
    code: str,
) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            timeout=timeout_seconds,
            close_fds=True,
            preexec_fn=lambda: _child_preexec(maximum_output_bytes),
        )
    except (OSError, subprocess.SubprocessError):
        _fail(code)
    if not isinstance(result.returncode, int) or result.returncode != 0:
        _fail(code)


def _derived_identity_recipient(identity: Path, *, code: str) -> str:
    keygen = _require_root_controlled_executable(
        FIXED_AGE_KEYGEN_BINARY,
        code="AGE_KEYGEN_BINARY_UNSAFE",
    )
    try:
        result = subprocess.run(
            [str(keygen), "-y", str(identity)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            timeout=30,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError):
        _fail(code)
    if not isinstance(result.returncode, int) or result.returncode != 0:
        _fail(code)
    output = result.stdout
    if not isinstance(output, bytes) or len(output) > 1024:
        _fail(code)
    try:
        recipient = output.decode("ascii")
    except UnicodeDecodeError:
        _fail(code)
    if not recipient.endswith("\n") or recipient.count("\n") != 1:
        _fail(code)
    return _require_recipient(recipient[:-1], code=code)


def _require_object_key(value: object, *, code: str) -> str:
    if not isinstance(value, str) or OBJECT_KEY_RE.fullmatch(value) is None:
        _fail(code)
    parts = value.split("/")
    if not parts or any(part in {"", ".", ".."} or part.lower() in _MUTABLE_OBJECT_COMPONENTS for part in parts):
        _fail(code)
    return value


def _require_version_id(value: object, *, code: str) -> str:
    if not isinstance(value, str) or VERSION_ID_RE.fullmatch(value) is None:
        _fail(code)
    if value.lower() in _MUTABLE_VERSION_IDS:
        _fail(code)
    return value


def _require_private_fd(
    value: object,
    *,
    code: str,
    maximum_bytes: int,
    allow_modes: frozenset[int],
    require_empty: bool,
) -> tuple[int, os.stat_result]:
    """Validate an already-open private regular file without closing it."""

    if type(value) is not int or value < 0:
        _fail(code)
    try:
        metadata = os.fstat(value)
        offset = os.lseek(value, 0, os.SEEK_CUR)
    except OSError:
        _fail(code)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) not in allow_modes
        or metadata.st_size < 0
        or metadata.st_size > maximum_bytes
        or offset < 0
        or offset > metadata.st_size
        or (require_empty and (metadata.st_size != 0 or offset != 0))
    ):
        _fail(code)
    return value, metadata


def _verify_age_header_fd(descriptor: int, *, expected: os.stat_result, code: str) -> None:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        header = os.read(descriptor, len(_AGE_HEADER))
        after = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError:
        _fail(code)
    if (
        header != _AGE_HEADER
        or after.st_dev != expected.st_dev
        or after.st_ino != expected.st_ino
        or after.st_size != expected.st_size
        or after.st_mode != expected.st_mode
        or after.st_uid != expected.st_uid
        or after.st_nlink != expected.st_nlink
    ):
        _fail(code)


def _copy_private_fd_to_new(
    source_fd: int,
    *,
    expected: os.stat_result,
    destination: Path,
    maximum_bytes: int,
    source_modes: frozenset[int],
    code: str,
) -> None:
    """Copy a caller FD into a new private file and detect source mutation."""

    if not hasattr(os, "O_NOFOLLOW"):
        _fail("AGE_PLATFORM_NO_NOFOLLOW")
    destination_fd: int | None = None
    try:
        os.lseek(source_fd, 0, os.SEEK_SET)
        before = os.fstat(source_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in source_modes
            or before.st_dev != expected.st_dev
            or before.st_ino != expected.st_ino
            or before.st_size != expected.st_size
            or before.st_size < 1
            or before.st_size > maximum_bytes
        ):
            _fail(code)
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        os.fchmod(destination_fd, 0o600)
        total = 0
        while True:
            chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                _fail(code)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if not isinstance(written, int) or written <= 0:
                    _fail(code)
                view = view[written:]
        after = os.fstat(source_fd)
        if (
            total != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mode != before.st_mode
            or after.st_uid != before.st_uid
            or after.st_nlink != before.st_nlink
        ):
            _fail(code)
        os.fsync(destination_fd)
        copied = os.fstat(destination_fd)
        if (
            not stat.S_ISREG(copied.st_mode)
            or copied.st_uid != os.geteuid()
            or copied.st_nlink != 1
            or stat.S_IMODE(copied.st_mode) != 0o600
            or copied.st_size != total
        ):
            _fail(code)
        os.lseek(source_fd, 0, os.SEEK_SET)
    except OSError:
        _fail(code)
    finally:
        if destination_fd is not None:
            try:
                os.close(destination_fd)
            except OSError:
                pass


def _copy_private_file_to_fd(
    source: Path,
    destination_fd: int,
    *,
    destination_expected: os.stat_result,
    maximum_bytes: int,
    code: str,
) -> tuple[str, int]:
    """Write one checked private file into an empty caller-owned FD exactly once."""

    _require_private_input_file(
        source,
        code=code,
        maximum_bytes=maximum_bytes,
        allow_modes=frozenset({0o600}),
        minimum_bytes=1,
    )
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail(code)
    try:
        current_destination = os.fstat(destination_fd)
        position = os.lseek(destination_fd, 0, os.SEEK_CUR)
        if (
            current_destination.st_dev != destination_expected.st_dev
            or current_destination.st_ino != destination_expected.st_ino
            or current_destination.st_size != 0
            or current_destination.st_mode != destination_expected.st_mode
            or current_destination.st_uid != destination_expected.st_uid
            or current_destination.st_nlink != destination_expected.st_nlink
            or position != 0
        ):
            _fail(code)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                _fail(code)
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if not isinstance(written, int) or written <= 0:
                    _fail(code)
                view = view[written:]
        os.fsync(destination_fd)
        written = os.fstat(destination_fd)
        if (
            total < 1
            or written.st_dev != destination_expected.st_dev
            or written.st_ino != destination_expected.st_ino
            or written.st_size != total
            or written.st_mode != destination_expected.st_mode
            or written.st_uid != destination_expected.st_uid
            or written.st_nlink != destination_expected.st_nlink
        ):
            _fail(code)
        return digest.hexdigest(), total
    except OSError:
        _fail(code)
    finally:
        try:
            os.close(source_fd)
        except OSError:
            pass


class PhysicalAgeV1Encryptor:
    """One-recipient, default-disabled ``age-v1`` encryptor protocol adapter."""

    def __init__(self, config: PhysicalAgeV1EncryptorConfig):
        self._config = config

    def encrypt(
        self,
        *,
        recipient: str,
        plaintext_path: Path,
        ciphertext_path: Path,
    ) -> None:
        facts = _normalise_encryptor_config(self._config)
        supplied_recipient = _require_recipient(recipient, code="AGE_ENCRYPTOR_RECIPIENT_INVALID")
        if supplied_recipient != facts.recipient:
            _fail("AGE_ENCRYPTOR_RECIPIENT_MISMATCH")
        plaintext = _require_private_input_file(
            plaintext_path,
            code="AGE_ENCRYPTOR_PLAINTEXT_UNSAFE",
            maximum_bytes=facts.maximum_plaintext_bytes,
            allow_modes=frozenset({0o400, 0o600}),
            minimum_bytes=1,
        )
        destination = _require_new_private_destination(
            ciphertext_path,
            code="AGE_ENCRYPTOR_CIPHERTEXT_DESTINATION_UNSAFE",
        )
        age = _require_root_controlled_executable(FIXED_AGE_BINARY, code="AGE_BINARY_UNSAFE")
        temporary, work = _private_temporary_workspace(
            facts.workspace_root,
            prefix="physical-age-encrypt-",
            code="AGE_ENCRYPTOR_WORKSPACE_UNSAFE",
        )
        try:
            snapshot = work / "plaintext.snapshot"
            _copy_private_file_to_new(
                plaintext,
                snapshot,
                maximum_bytes=facts.maximum_plaintext_bytes,
                source_modes=frozenset({0o400, 0o600}),
                code="AGE_ENCRYPTOR_PLAINTEXT_UNSAFE",
            )
            produced = work / "ciphertext.age"
            _run_age_transform(
                [str(age), "-r", facts.recipient, "-o", str(produced), str(snapshot)],
                maximum_output_bytes=facts.maximum_ciphertext_bytes,
                timeout_seconds=900,
                code="AGE_ENCRYPTOR_COMMAND_FAILED",
            )
            _verify_private_output(
                produced,
                code="AGE_ENCRYPTOR_CIPHERTEXT_UNSAFE",
                minimum_bytes=len(_AGE_HEADER),
                maximum_bytes=facts.maximum_ciphertext_bytes,
                require_age_header=True,
            )
            try:
                _copy_private_file_to_new(
                    produced,
                    destination,
                    maximum_bytes=facts.maximum_ciphertext_bytes,
                    source_modes=frozenset({0o600}),
                    code="AGE_ENCRYPTOR_CIPHERTEXT_DESTINATION_UNSAFE",
                )
            except Exception:
                _remove_our_new_file(destination)
                raise
        finally:
            temporary.cleanup()


class PhysicalAgeV1Decryptor:
    """Identity-verified, default-disabled ``age-v1`` decryptor adapter."""

    def __init__(self, config: PhysicalAgeV1DecryptorConfig):
        self._config = config

    def decrypt(
        self,
        *,
        expected_recipient: str,
        ciphertext_path: Path,
        plaintext_path: Path,
    ) -> None:
        facts = _normalise_decryptor_config(self._config)
        supplied_recipient = _require_recipient(expected_recipient, code="AGE_DECRYPTOR_RECIPIENT_INVALID")
        if supplied_recipient != facts.recipient:
            _fail("AGE_DECRYPTOR_RECIPIENT_MISMATCH")
        ciphertext = _require_private_input_file(
            ciphertext_path,
            code="AGE_DECRYPTOR_CIPHERTEXT_UNSAFE",
            maximum_bytes=facts.maximum_ciphertext_bytes,
            allow_modes=frozenset({0o600}),
            minimum_bytes=len(_AGE_HEADER),
        )
        _verify_private_output(
            ciphertext,
            code="AGE_DECRYPTOR_CIPHERTEXT_UNSAFE",
            minimum_bytes=len(_AGE_HEADER),
            maximum_bytes=facts.maximum_ciphertext_bytes,
            require_age_header=True,
        )
        destination = _require_new_private_destination(
            plaintext_path,
            code="AGE_DECRYPTOR_PLAINTEXT_DESTINATION_UNSAFE",
        )
        age = _require_root_controlled_executable(FIXED_AGE_BINARY, code="AGE_BINARY_UNSAFE")
        temporary, work = _private_temporary_workspace(
            facts.workspace_root,
            prefix="physical-age-decrypt-",
            code="AGE_DECRYPTOR_WORKSPACE_UNSAFE",
        )
        try:
            identity_snapshot = work / "identity.snapshot"
            _copy_private_file_to_new(
                facts.identity_path,
                identity_snapshot,
                maximum_bytes=MAX_PHYSICAL_AGE_IDENTITY_BYTES,
                source_modes=frozenset({0o400, 0o600}),
                code="AGE_DECRYPTOR_IDENTITY_UNSAFE",
            )
            derived_recipient = _derived_identity_recipient(
                identity_snapshot,
                code="AGE_DECRYPTOR_IDENTITY_RECIPIENT_UNVERIFIED",
            )
            if derived_recipient != facts.recipient:
                _fail("AGE_DECRYPTOR_IDENTITY_RECIPIENT_MISMATCH")
            snapshot = work / "ciphertext.snapshot"
            _copy_private_file_to_new(
                ciphertext,
                snapshot,
                maximum_bytes=facts.maximum_ciphertext_bytes,
                source_modes=frozenset({0o600}),
                code="AGE_DECRYPTOR_CIPHERTEXT_UNSAFE",
            )
            _verify_private_output(
                snapshot,
                code="AGE_DECRYPTOR_CIPHERTEXT_UNSAFE",
                minimum_bytes=len(_AGE_HEADER),
                maximum_bytes=facts.maximum_ciphertext_bytes,
                require_age_header=True,
            )
            produced = work / "plaintext.output"
            _run_age_transform(
                [
                    str(age),
                    "--decrypt",
                    "-i",
                    str(identity_snapshot),
                    "-o",
                    str(produced),
                    str(snapshot),
                ],
                maximum_output_bytes=facts.maximum_plaintext_bytes,
                timeout_seconds=900,
                code="AGE_DECRYPTOR_COMMAND_FAILED",
            )
            _verify_private_output(
                produced,
                code="AGE_DECRYPTOR_PLAINTEXT_UNSAFE",
                minimum_bytes=1,
                maximum_bytes=facts.maximum_plaintext_bytes,
                require_age_header=False,
            )
            try:
                _copy_private_file_to_new(
                    produced,
                    destination,
                    maximum_bytes=facts.maximum_plaintext_bytes,
                    source_modes=frozenset({0o600}),
                    code="AGE_DECRYPTOR_PLAINTEXT_DESTINATION_UNSAFE",
                )
            except Exception:
                _remove_our_new_file(destination)
                raise
        finally:
            temporary.cleanup()


class PhysicalAgeV1FdDecryptor:
    """FD-safe adapter for :class:`PhysicalWalDecryptor` receiver staging.

    The physical WAL receiver owns secure candidate file descriptors already.
    ``age`` itself accepts paths, so this bridge snapshots both supplied
    ciphertext and the configured identity in a private workspace, invokes
    the fixed binary only on those snapshots, then copies checked plaintext
    into the supplied empty destination FD.  It neither opens Object Storage
    nor interprets a PostgreSQL artifact.
    """

    def __init__(self, config: PhysicalAgeV1DecryptorConfig):
        self._config = config

    def decrypt_to_fd(
        self,
        *,
        ciphertext_fd: int,
        destination_fd: int,
        object_key: str,
        version_id: str,
        expected_age_recipient: str,
    ) -> PhysicalWalDecryptionReadback:
        facts = _normalise_decryptor_config(self._config)
        supplied_recipient = _require_recipient(
            expected_age_recipient,
            code="AGE_FD_DECRYPTOR_RECIPIENT_INVALID",
        )
        if supplied_recipient != facts.recipient:
            _fail("AGE_FD_DECRYPTOR_RECIPIENT_MISMATCH")
        safe_key = _require_object_key(object_key, code="AGE_FD_DECRYPTOR_OBJECT_IDENTITY_INVALID")
        safe_version = _require_version_id(version_id, code="AGE_FD_DECRYPTOR_OBJECT_IDENTITY_INVALID")
        safe_ciphertext_fd, ciphertext_expected = _require_private_fd(
            ciphertext_fd,
            code="AGE_FD_DECRYPTOR_CIPHERTEXT_UNSAFE",
            maximum_bytes=facts.maximum_ciphertext_bytes,
            allow_modes=frozenset({0o400, 0o600}),
            require_empty=False,
        )
        if ciphertext_expected.st_size < len(_AGE_HEADER):
            _fail("AGE_FD_DECRYPTOR_CIPHERTEXT_UNSAFE")
        _verify_age_header_fd(
            safe_ciphertext_fd,
            expected=ciphertext_expected,
            code="AGE_FD_DECRYPTOR_CIPHERTEXT_UNSAFE",
        )
        safe_destination_fd, destination_expected = _require_private_fd(
            destination_fd,
            code="AGE_FD_DECRYPTOR_DESTINATION_UNSAFE",
            maximum_bytes=facts.maximum_plaintext_bytes,
            allow_modes=frozenset({0o600}),
            require_empty=True,
        )
        age = _require_root_controlled_executable(FIXED_AGE_BINARY, code="AGE_BINARY_UNSAFE")
        temporary, work = _private_temporary_workspace(
            facts.workspace_root,
            prefix="physical-age-fd-decrypt-",
            code="AGE_FD_DECRYPTOR_WORKSPACE_UNSAFE",
        )
        try:
            identity_snapshot = work / "identity.snapshot"
            _copy_private_file_to_new(
                facts.identity_path,
                identity_snapshot,
                maximum_bytes=MAX_PHYSICAL_AGE_IDENTITY_BYTES,
                source_modes=frozenset({0o400, 0o600}),
                code="AGE_DECRYPTOR_IDENTITY_UNSAFE",
            )
            derived_recipient = _derived_identity_recipient(
                identity_snapshot,
                code="AGE_DECRYPTOR_IDENTITY_RECIPIENT_UNVERIFIED",
            )
            if derived_recipient != facts.recipient:
                _fail("AGE_DECRYPTOR_IDENTITY_RECIPIENT_MISMATCH")
            ciphertext_snapshot = work / "ciphertext.snapshot"
            _copy_private_fd_to_new(
                safe_ciphertext_fd,
                expected=ciphertext_expected,
                destination=ciphertext_snapshot,
                maximum_bytes=facts.maximum_ciphertext_bytes,
                source_modes=frozenset({0o400, 0o600}),
                code="AGE_FD_DECRYPTOR_CIPHERTEXT_UNSAFE",
            )
            _verify_private_output(
                ciphertext_snapshot,
                code="AGE_FD_DECRYPTOR_CIPHERTEXT_UNSAFE",
                minimum_bytes=len(_AGE_HEADER),
                maximum_bytes=facts.maximum_ciphertext_bytes,
                require_age_header=True,
            )
            produced = work / "plaintext.output"
            _run_age_transform(
                [
                    str(age),
                    "--decrypt",
                    "-i",
                    str(identity_snapshot),
                    "-o",
                    str(produced),
                    str(ciphertext_snapshot),
                ],
                maximum_output_bytes=facts.maximum_plaintext_bytes,
                timeout_seconds=900,
                code="AGE_FD_DECRYPTOR_COMMAND_FAILED",
            )
            _verify_private_output(
                produced,
                code="AGE_FD_DECRYPTOR_PLAINTEXT_UNSAFE",
                minimum_bytes=1,
                maximum_bytes=facts.maximum_plaintext_bytes,
                require_age_header=False,
            )
            plaintext_sha256, plaintext_bytes = _copy_private_file_to_fd(
                produced,
                safe_destination_fd,
                destination_expected=destination_expected,
                maximum_bytes=facts.maximum_plaintext_bytes,
                code="AGE_FD_DECRYPTOR_DESTINATION_UNSAFE",
            )
            return PhysicalWalDecryptionReadback(
                object_key=safe_key,
                version_id=safe_version,
                age_recipient=facts.recipient,
                plaintext_sha256=plaintext_sha256,
                plaintext_bytes=plaintext_bytes,
            )
        finally:
            temporary.cleanup()
