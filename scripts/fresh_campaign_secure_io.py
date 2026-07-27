"""Fail-closed Git provenance and dirfd-bound campaign material I/O."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import secrets
import stat
import subprocess
from typing import Callable, Iterable


AT_FDCWD = -100
RENAME_NOREPLACE = 1
SAFE_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}
DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
FILE_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class FreshCampaignSecureIOError(RuntimeError):
    """A security-sensitive release or filesystem binding failed."""


def _run_git(repo_root: Path, arguments: list[str]) -> bytes:
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(repo_root), *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            env=SAFE_GIT_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FreshCampaignSecureIOError("exact Git release inspection failed") from exc
    if result.returncode != 0:
        raise FreshCampaignSecureIOError("exact Git release inspection failed")
    return result.stdout


def _git_relative(repo_root: Path, path: Path) -> str:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or Path(os.path.normpath(path)) != path
    ):
        raise FreshCampaignSecureIOError("bound release path is not normalized")
    try:
        relative = path.relative_to(repo_root)
    except ValueError as exc:
        raise FreshCampaignSecureIOError(
            "bound release path is outside the exact repository"
        ) from exc
    value = relative.as_posix()
    if not value or value.startswith(".git/"):
        raise FreshCampaignSecureIOError("bound release path is invalid")
    return value


def _check_directory_metadata(
    metadata: os.stat_result,
    *,
    leaf: bool,
    require_private_leaf: bool,
) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    sticky_root = metadata.st_uid == 0 and bool(mode & stat.S_ISVTX)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or (
            mode & 0o022
            and not sticky_root
        )
        or (leaf and require_private_leaf and mode & 0o077)
    ):
        raise FreshCampaignSecureIOError(
            "path ancestor is not a root-controlled directory"
        )


def open_root_directory(
    path: Path,
    *,
    require_private_leaf: bool,
) -> int:
    """Open an absolute directory one no-follow component at a time."""

    if (
        os.geteuid() != 0
        or not path.is_absolute()
        or ".." in path.parts
        or Path(os.path.normpath(path)) != path
    ):
        raise FreshCampaignSecureIOError(
            "secure directory path requires root and normalized absolute form"
        )
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        root_metadata = os.fstat(descriptor)
        _check_directory_metadata(
            root_metadata,
            leaf=path == Path("/"),
            require_private_leaf=require_private_leaf,
        )
        components = path.parts[1:]
        for index, component in enumerate(components):
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            _check_directory_metadata(
                os.fstat(descriptor),
                leaf=index == len(components) - 1,
                require_private_leaf=require_private_leaf,
            )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def assert_path_matches_directory_fd(
    path: Path,
    descriptor: int,
    *,
    require_private_leaf: bool,
) -> None:
    rebound = open_root_directory(
        path,
        require_private_leaf=require_private_leaf,
    )
    try:
        expected = os.fstat(descriptor)
        observed = os.fstat(rebound)
        if (expected.st_dev, expected.st_ino) != (observed.st_dev, observed.st_ino):
            raise FreshCampaignSecureIOError(
                "secure directory path binding changed during the operation"
            )
    finally:
        os.close(rebound)


def read_secure_root_file(
    path: Path,
    *,
    label: str,
    expected_mode: int,
    max_size: int,
) -> bytes:
    """Read a stable root-owned file through its bound parent directory."""

    if (
        not path.is_absolute()
        or ".." in path.parts
        or Path(os.path.normpath(path)) != path
        or path.name in {"", ".", ".."}
    ):
        raise FreshCampaignSecureIOError(f"{label} path is invalid")
    parent_fd = open_root_directory(path.parent, require_private_leaf=True)
    descriptor = -1
    try:
        descriptor = os.open(path.name, FILE_READ_FLAGS, dir_fd=parent_fd)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != expected_mode
            or not 1 <= before.st_size <= max_size
        ):
            raise FreshCampaignSecureIOError(f"{label} is not one exact private file")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                raise FreshCampaignSecureIOError(f"{label} changed while read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FreshCampaignSecureIOError(f"{label} grew while read")
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise FreshCampaignSecureIOError(f"{label} changed while read")
        assert_path_matches_directory_fd(
            path.parent,
            parent_fd,
            require_private_leaf=True,
        )
        return b"".join(chunks)
    except OSError as exc:
        raise FreshCampaignSecureIOError(f"{label} is unavailable or unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _read_bound_release_file(path: Path, *, max_size: int) -> bytes:
    """Read a public tracked file without accepting links or path swaps."""

    parent_fd = open_root_directory(path.parent, require_private_leaf=False)
    descriptor = -1
    try:
        descriptor = os.open(path.name, FILE_READ_FLAGS, dir_fd=parent_fd)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= max_size
        ):
            raise FreshCampaignSecureIOError("bound release file is unsafe")
        payload = b""
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                raise FreshCampaignSecureIOError("bound release file changed")
            payload += chunk
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if any(
            getattr(before, field) != getattr(after, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        ):
            raise FreshCampaignSecureIOError("bound release file changed")
        assert_path_matches_directory_fd(
            path.parent,
            parent_fd,
            require_private_leaf=False,
        )
        return payload
    except OSError as exc:
        raise FreshCampaignSecureIOError("bound release file is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


@dataclass(frozen=True)
class ExactGitRelease:
    repo_root: Path
    release_sha: str
    bound_paths: tuple[str, ...]
    blob_paths: tuple[str, ...]
    bound_sha256: dict[str, str]
    blobs: dict[str, bytes]
    blob_sha256: dict[str, str]

    def recheck(self) -> None:
        current = prove_exact_git_release(
            repo_root=self.repo_root,
            release_sha=self.release_sha,
            bound_files=tuple(self.repo_root / value for value in self.bound_paths),
            blob_paths=self.blob_paths,
        )
        if (
            current.bound_sha256 != self.bound_sha256
            or current.blob_sha256 != self.blob_sha256
        ):
            raise FreshCampaignSecureIOError(
                "exact Git release binding changed before publication"
            )


def prove_exact_git_release(
    *,
    repo_root: Path,
    release_sha: str,
    bound_files: Iterable[Path],
    blob_paths: Iterable[str] = (),
) -> ExactGitRelease:
    """Prove clean HEAD identity and bind scripts plus requested Git blobs."""

    if (
        os.geteuid() != 0
        or not repo_root.is_absolute()
        or Path(os.path.normpath(repo_root)) != repo_root
        or not isinstance(release_sha, str)
        or len(release_sha) != 40
        or any(character not in "0123456789abcdef" for character in release_sha)
    ):
        raise FreshCampaignSecureIOError("exact Git release request is invalid")
    head = _run_git(repo_root, ["rev-parse", "--verify", "HEAD^{commit}"]).decode(
        "ascii"
    ).strip()
    if head != release_sha:
        raise FreshCampaignSecureIOError("Git HEAD differs from the requested release")
    if _run_git(
        repo_root,
        ["status", "--porcelain=v1", "--untracked-files=all"],
    ):
        raise FreshCampaignSecureIOError("exact Git release worktree is not clean")
    tracked_paths = _run_git(repo_root, ["ls-files", "-z"]).split(b"\0")
    ignored_paths = _run_git(
        repo_root,
        ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
    ).split(b"\0")
    for raw_path in (*tracked_paths, *ignored_paths):
        if not raw_path:
            continue
        try:
            value = raw_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FreshCampaignSecureIOError(
                "exact Git release contains a non-UTF-8 path"
            ) from exc
        path = Path(value)
        if (
            path.suffix in {".pyc", ".pyo"}
            or "__pycache__" in path.parts
        ):
            raise FreshCampaignSecureIOError(
                "exact Git release contains forbidden Python bytecode"
            )

    bound_relative = tuple(
        sorted({_git_relative(repo_root, path) for path in bound_files})
    )
    if not bound_relative:
        raise FreshCampaignSecureIOError("no exact release script path was bound")
    bound_hashes: dict[str, str] = {}
    for relative in bound_relative:
        git_bytes = _run_git(repo_root, ["show", f"{release_sha}:{relative}"])
        disk_bytes = _read_bound_release_file(
            repo_root / relative,
            max_size=16 * 1024 * 1024,
        )
        if not git_bytes or disk_bytes != git_bytes:
            raise FreshCampaignSecureIOError(
                "executed script differs from the exact Git release blob"
            )
        bound_hashes[relative] = hashlib.sha256(git_bytes).hexdigest()

    normalized_blob_paths: list[str] = []
    blobs: dict[str, bytes] = {}
    blob_hashes: dict[str, str] = {}
    for raw in sorted(set(blob_paths)):
        relative = Path(raw)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != raw
            or not raw
            or raw.startswith(".git/")
        ):
            raise FreshCampaignSecureIOError("requested Git blob path is invalid")
        payload = _run_git(repo_root, ["show", f"{release_sha}:{raw}"])
        if not payload or len(payload) > 16 * 1024 * 1024:
            raise FreshCampaignSecureIOError("requested Git blob is empty or oversized")
        normalized_blob_paths.append(raw)
        blobs[raw] = payload
        blob_hashes[raw] = hashlib.sha256(payload).hexdigest()
    return ExactGitRelease(
        repo_root=repo_root,
        release_sha=release_sha,
        bound_paths=bound_relative,
        blob_paths=tuple(normalized_blob_paths),
        bound_sha256=bound_hashes,
        blobs=blobs,
        blob_sha256=blob_hashes,
    )


def _open_relative_directory(parent_fd: int, parts: tuple[str, ...]) -> int:
    descriptor = os.dup(parent_fd)
    try:
        for component in parts:
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise FreshCampaignSecureIOError(
                    "transaction subdirectory is not root-owned mode-0700"
                )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _remove_tree_contents(descriptor: int) -> None:
    for name in os.listdir(descriptor):
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            child = os.open(name, DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                _remove_tree_contents(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=descriptor)
        else:
            os.unlink(name, dir_fd=descriptor)


class SecureOutputDirectory:
    """One owner-only directory transaction bound to a single parent fd."""

    def __init__(self, output: Path) -> None:
        if (
            os.geteuid() != 0
            or not output.is_absolute()
            or ".." in output.parts
            or Path(os.path.normpath(output)) != output
            or output.name in {"", ".", ".."}
        ):
            raise FreshCampaignSecureIOError("secure output path is invalid")
        self.output = output
        self.parent_path = output.parent
        self.parent_fd = open_root_directory(
            self.parent_path,
            require_private_leaf=True,
        )
        self.output_name = output.name
        self.temporary_name = (
            f".{self.output_name}.creating-{os.getpid()}-{secrets.token_hex(8)}"
        )
        self.temporary_fd = -1
        self.published = False
        try:
            try:
                os.stat(self.output_name, dir_fd=self.parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FreshCampaignSecureIOError("secure output already exists")
            os.mkdir(self.temporary_name, mode=0o700, dir_fd=self.parent_fd)
            self.temporary_fd = os.open(
                self.temporary_name,
                DIRECTORY_FLAGS,
                dir_fd=self.parent_fd,
            )
            metadata = os.fstat(self.temporary_fd)
            if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise FreshCampaignSecureIOError(
                    "secure output transaction directory is unsafe"
                )
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _relative_parts(relative: str) -> tuple[str, ...]:
        path = Path(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != relative
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise FreshCampaignSecureIOError("transaction relative path is invalid")
        return path.parts

    def mkdir(self, relative: str) -> None:
        parts = self._relative_parts(relative)
        parent = _open_relative_directory(self.temporary_fd, parts[:-1])
        try:
            os.mkdir(parts[-1], mode=0o700, dir_fd=parent)
            child = os.open(parts[-1], DIRECTORY_FLAGS, dir_fd=parent)
            try:
                metadata = os.fstat(child)
                if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
                    raise FreshCampaignSecureIOError(
                        "transaction directory mode is unsafe"
                    )
            finally:
                os.close(child)
            os.fsync(parent)
        finally:
            os.close(parent)

    def write(self, relative: str, payload: bytes, *, mode: int) -> None:
        parts = self._relative_parts(relative)
        if (
            not isinstance(payload, bytes)
            or not payload
            or len(payload) > 16 * 1024 * 1024
            or mode not in {0o600, 0o640, 0o644}
        ):
            raise FreshCampaignSecureIOError("transaction file payload/mode is invalid")
        parent = _open_relative_directory(self.temporary_fd, parts[:-1])
        descriptor = -1
        try:
            descriptor = os.open(
                parts[-1],
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                mode,
                dir_fd=parent,
            )
            offset = 0
            while offset < len(payload):
                count = os.write(descriptor, payload[offset:])
                if count <= 0:
                    raise FreshCampaignSecureIOError(
                        "transaction file write made no progress"
                    )
                offset += count
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.fsync(parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(parent)

    def _rename_noreplace(self) -> None:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise FreshCampaignSecureIOError(
                "atomic no-replace directory publication is unavailable"
            )
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            self.parent_fd,
            os.fsencode(self.temporary_name),
            self.parent_fd,
            os.fsencode(self.output_name),
            RENAME_NOREPLACE,
        )
        if result != 0:
            error = ctypes.get_errno()
            if error in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FreshCampaignSecureIOError("secure output already exists")
            raise FreshCampaignSecureIOError(
                "atomic no-replace directory publication failed"
            ) from OSError(error, os.strerror(error))

    def publish(self, *, before_publish: Callable[[], None]) -> None:
        if self.published or self.temporary_fd < 0:
            raise FreshCampaignSecureIOError("output transaction is not publishable")
        before_publish()
        assert_path_matches_directory_fd(
            self.parent_path,
            self.parent_fd,
            require_private_leaf=True,
        )
        os.fsync(self.temporary_fd)
        self._rename_noreplace()
        self.published = True
        os.fsync(self.parent_fd)
        try:
            assert_path_matches_directory_fd(
                self.parent_path,
                self.parent_fd,
                require_private_leaf=True,
            )
        except BaseException:
            final_fd = os.open(self.output_name, DIRECTORY_FLAGS, dir_fd=self.parent_fd)
            try:
                _remove_tree_contents(final_fd)
            finally:
                os.close(final_fd)
            os.rmdir(self.output_name, dir_fd=self.parent_fd)
            os.fsync(self.parent_fd)
            self.published = False
            raise

    def close(self) -> None:
        if getattr(self, "temporary_fd", -1) >= 0:
            if not getattr(self, "published", False):
                try:
                    _remove_tree_contents(self.temporary_fd)
                    os.rmdir(self.temporary_name, dir_fd=self.parent_fd)
                    os.fsync(self.parent_fd)
                except FileNotFoundError:
                    pass
            os.close(self.temporary_fd)
            self.temporary_fd = -1
        if getattr(self, "parent_fd", -1) >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1

    def __enter__(self) -> "SecureOutputDirectory":
        return self

    def __exit__(self, _kind, _value, _traceback) -> None:  # noqa: ANN001
        self.close()


def read_secure_material_tree(root: Path) -> dict[str, tuple[bytes, int]]:
    """Return an exact no-follow inventory of one generated material tree."""

    parent_fd = open_root_directory(root.parent, require_private_leaf=True)
    root_fd = -1
    try:
        root_fd = os.open(root.name, DIRECTORY_FLAGS, dir_fd=parent_fd)
        metadata = os.fstat(root_fd)
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise FreshCampaignSecureIOError("material root is not root-owned mode-0700")
        result: dict[str, tuple[bytes, int]] = {}

        def walk(descriptor: int, prefix: str) -> None:
            for name in sorted(os.listdir(descriptor)):
                relative = f"{prefix}/{name}" if prefix else name
                item = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(item.st_mode) and not stat.S_ISLNK(item.st_mode):
                    if item.st_uid != 0 or stat.S_IMODE(item.st_mode) != 0o700:
                        raise FreshCampaignSecureIOError(
                            "material subdirectory mode is unsafe"
                        )
                    child = os.open(name, DIRECTORY_FLAGS, dir_fd=descriptor)
                    try:
                        walk(child, relative)
                    finally:
                        os.close(child)
                    continue
                if (
                    not stat.S_ISREG(item.st_mode)
                    or item.st_uid != 0
                    or item.st_nlink != 1
                    or stat.S_IMODE(item.st_mode) not in {0o600, 0o640, 0o644}
                    or not 1 <= item.st_size <= 16 * 1024 * 1024
                ):
                    raise FreshCampaignSecureIOError("material file is unsafe")
                child = os.open(name, FILE_READ_FLAGS, dir_fd=descriptor)
                try:
                    before = os.fstat(child)
                    payload = b""
                    remaining = before.st_size
                    while remaining:
                        chunk = os.read(child, min(65536, remaining))
                        if not chunk:
                            raise FreshCampaignSecureIOError(
                                "material file changed while read"
                            )
                        payload += chunk
                        remaining -= len(chunk)
                    after = os.fstat(child)
                    if any(
                        getattr(before, field) != getattr(after, field)
                        for field in (
                            "st_dev",
                            "st_ino",
                            "st_mode",
                            "st_uid",
                            "st_nlink",
                            "st_size",
                            "st_mtime_ns",
                            "st_ctime_ns",
                        )
                    ):
                        raise FreshCampaignSecureIOError(
                            "material file changed while read"
                        )
                    result[relative] = (
                        payload,
                        stat.S_IMODE(before.st_mode),
                    )
                finally:
                    os.close(child)

        walk(root_fd, "")
        assert_path_matches_directory_fd(
            root.parent,
            parent_fd,
            require_private_leaf=True,
        )
        return result
    except OSError as exc:
        raise FreshCampaignSecureIOError(
            "material tree is unavailable or unsafe"
        ) from exc
    finally:
        if root_fd >= 0:
            os.close(root_fd)
        os.close(parent_fd)
