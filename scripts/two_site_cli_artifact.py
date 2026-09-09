"""Safe disposable CLI artifact preparation helper module."""

from __future__ import annotations

import contextlib
import hashlib
import os
import pathlib
import stat
import tempfile
from typing import Iterator

__all__ = ["CliArtifactError", "prepare_disposable_cli"]


class CliArtifactError(ValueError):
    """Raised when a CLI source artifact cannot be safely validated or prepared."""


@contextlib.contextmanager
def prepare_disposable_cli(
    source: pathlib.Path,
    expected_sha256: str | None,
    require_read_only: bool = True,
) -> Iterator[tuple[pathlib.Path, dict[str, str]]]:
    """Prepare a verified, disposable executable copy of a pinned source artifact."""
    if not isinstance(source, pathlib.Path):
        raise CliArtifactError(f"source must be a pathlib.Path: {source!r}")
    if not source.is_absolute():
        raise CliArtifactError(f"Source path must be absolute: {source}")
    if ".." in source.parts or "." in source.parts:
        raise CliArtifactError(f"Source path contains unresolved components: {source}")
    if source.is_symlink():
        raise CliArtifactError(f"Source path is a symlink: {source}")
    try:
        resolved_source = source.resolve(strict=True)
    except FileNotFoundError:
        raise
    except (OSError, RuntimeError) as exc:
        raise CliArtifactError(f"Cannot resolve source path {source}: {exc}") from exc
    if resolved_source != source:
        raise CliArtifactError(f"Source path is unresolved or contains symlinks: {source} -> {resolved_source}")

    expected_hex: str | None = None
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not expected_sha256.strip():
            raise CliArtifactError("expected_sha256 must be a non-empty string")
        expected_hex = expected_sha256.strip().lower()

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    disposable_path: pathlib.Path | None = None
    active_exc: BaseException | None = None

    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            source_fd = os.open(source, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise CliArtifactError(f"Failed to open source file {source}: {exc}") from exc

        try:
            init_fstat = os.fstat(source_fd)
            init_lstat = os.lstat(source)
            if not stat.S_ISREG(init_fstat.st_mode) or not stat.S_ISREG(init_lstat.st_mode):
                raise CliArtifactError(f"Source is not a regular file: {source}")
            if stat.S_ISLNK(init_lstat.st_mode):
                raise CliArtifactError(f"Source path is a symlink: {source}")
            if init_fstat.st_dev != init_lstat.st_dev or init_fstat.st_ino != init_lstat.st_ino:
                raise CliArtifactError(f"Source descriptor does not match named path: {source}")
            if init_fstat.st_nlink != 1 or init_lstat.st_nlink != 1:
                raise CliArtifactError(f"Source hard link count is not 1: {source}")
            euid = os.geteuid()
            if init_fstat.st_uid != euid or init_lstat.st_uid != euid:
                raise CliArtifactError(f"Source owner UID does not match effective UID: {source}")
            if require_read_only and ((init_fstat.st_mode & 0o222) != 0 or (init_lstat.st_mode & 0o222) != 0):
                raise CliArtifactError(f"Source has write permission bits: {source}")

            temp_dir = tempfile.TemporaryDirectory(prefix="disposable_cli_")
            temp_dir_path = pathlib.Path(temp_dir.name)
            os.chmod(temp_dir_path, 0o700)

            disposable_path = temp_dir_path / source.name
            out_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            out_fd = os.open(disposable_path, out_flags, 0o700)
            try:
                hasher = hashlib.sha256()
                bytes_copied = 0
                while True:
                    chunk = os.read(source_fd, 65536)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(out_fd, view)
                        view = view[written:]
                    bytes_copied += len(chunk)
            finally:
                os.close(out_fd)

            digest = hasher.hexdigest()
            if expected_hex is not None and digest != expected_hex:
                raise CliArtifactError(f"SHA-256 mismatch for {source}: expected {expected_hex}, got {digest}")

            post_fstat = os.fstat(source_fd)
            post_lstat = os.lstat(source)
            if (
                post_fstat.st_dev != init_fstat.st_dev
                or post_fstat.st_ino != init_fstat.st_ino
                or post_fstat.st_size != init_fstat.st_size
                or post_fstat.st_size != bytes_copied
                or post_fstat.st_mtime_ns != init_fstat.st_mtime_ns
                or post_fstat.st_ctime_ns != init_fstat.st_ctime_ns
                or post_fstat.st_nlink != 1
                or not stat.S_ISREG(post_fstat.st_mode)
                or post_fstat.st_uid != euid
                or (require_read_only and (post_fstat.st_mode & 0o222) != 0)
            ):
                raise CliArtifactError(f"Source descriptor was mutated during copying: {source}")

            if (
                post_lstat.st_dev != init_fstat.st_dev
                or post_lstat.st_ino != init_fstat.st_ino
                or post_lstat.st_size != init_fstat.st_size
                or post_lstat.st_mtime_ns != init_fstat.st_mtime_ns
                or post_lstat.st_ctime_ns != init_fstat.st_ctime_ns
                or post_lstat.st_nlink != 1
                or stat.S_ISLNK(post_lstat.st_mode)
                or not stat.S_ISREG(post_lstat.st_mode)
                or post_lstat.st_uid != euid
                or (require_read_only and (post_lstat.st_mode & 0o222) != 0)
            ):
                raise CliArtifactError(f"Source named path was replaced or mutated during copying: {source}")

            try:
                final_resolved = source.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise CliArtifactError(f"Failed to resolve source path after copying: {exc}") from exc
            if final_resolved != source:
                raise CliArtifactError(f"Source resolution changed after copying: {source} -> {final_resolved}")

            os.chmod(disposable_path, 0o700)
            evidence: dict[str, str] = {
                "source_path": str(resolved_source),
                "sha256": digest,
            }
        finally:
            os.close(source_fd)

        yield disposable_path, evidence
    except BaseException as exc:
        active_exc = exc
        raise
    finally:
        try:
            if disposable_path is not None and os.path.lexists(disposable_path):
                os.unlink(disposable_path)
            if temp_dir is not None:
                temp_dir.cleanup()
        except Exception:
            if active_exc is None:
                raise
