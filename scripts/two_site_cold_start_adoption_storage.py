#!/usr/bin/env python3
"""Dormant private disk storage for terminal cold-start adoption records under AE03.

Precondition: Caller MUST hold the existing exclusive Supervisor lock throughout
all operations. Do not create another lock.

Boundary and authority constraints:
1. Dormant storage only: contains NO startup hook, NO Provider/Broker execution,
   NO wake, NO replay, and NO retention or deletion authority.
2. Does NOT authorize startup.
3. Path is exactly state_root/adoptions/<rescope_epoch_basename(new_repository)>.json,
   with private 0700 state_root/adoptions, regular 0600 single-link no-overwrite leaf;
   no mutable active pointer and no overwriting existing adoption.
4. Independent verification: loads real old terminal epoch and accepted checkpoint,
   computing checkpoint SHA-256 directly from re-read stable canonical bytes.
5. Fail-closed publication: ambiguous post-link retention preserves final record on any
   failure after hard-link; temp-only failure cleans only created temp.
"""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import stat
import sys
from typing import Any

try:
    from scripts.two_site_codex_autopilot import (
        MAX_ACCEPTED_CHECKPOINT_BYTES,
        _load_accepted_checkpoint,
        rescope_epoch_basename,
    )
except ImportError:
    try:
        from two_site_codex_autopilot import (  # type: ignore[no-redef]
            MAX_ACCEPTED_CHECKPOINT_BYTES,
            _load_accepted_checkpoint,
            rescope_epoch_basename,
        )
    except ImportError:
        MAX_ACCEPTED_CHECKPOINT_BYTES = 16384
        from two_site_codex_autopilot import (  # type: ignore[no-redef]
            _load_accepted_checkpoint,
            rescope_epoch_basename,
        )

try:
    from scripts.two_site_proposal_runner import serialize_json, unique_json
except ImportError:
    from two_site_proposal_runner import serialize_json, unique_json  # type: ignore[no-redef]

try:
    from scripts.two_site_cold_start_epoch import (
        ColdStartEpochError,
        inspect_terminal_rescope_epoch,
    )
except ImportError:
    from two_site_cold_start_epoch import (  # type: ignore[no-redef]
        ColdStartEpochError,
        inspect_terminal_rescope_epoch,
    )

try:
    from scripts.two_site_cold_start_adoption_record import (
        ColdStartAdoptionRecordError,
        MAX_TERMINAL_ADOPTION_RECORD_BYTES,
        TERMINAL_ADOPTION_KIND,
        TERMINAL_ADOPTION_SCHEMA_VERSION,
        build_terminal_adoption_record,
        parse_terminal_adoption_record,
        serialize_terminal_adoption_record,
        validate_terminal_adoption_record,
    )
except ImportError:
    from two_site_cold_start_adoption_record import (  # type: ignore[no-redef]
        ColdStartAdoptionRecordError,
        MAX_TERMINAL_ADOPTION_RECORD_BYTES,
        TERMINAL_ADOPTION_KIND,
        TERMINAL_ADOPTION_SCHEMA_VERSION,
        build_terminal_adoption_record,
        parse_terminal_adoption_record,
        serialize_terminal_adoption_record,
        validate_terminal_adoption_record,
    )

ADOPTIONS_DIRNAME: str = "adoptions"


class ColdStartAdoptionStorageError(Exception):
    """Raised when cold-start adoption record reading, writing, or validation fails."""


class ColdStartAdoptionStorageAmbiguousPostPublicationError(ColdStartAdoptionStorageError):
    """Raised when failure or drift occurs after target adoption file hard-link."""


ColdStartAdoptionStorageAmbiguousError = ColdStartAdoptionStorageAmbiguousPostPublicationError
ColdStartAdoptionStorageAmbiguousPostLinkError = ColdStartAdoptionStorageAmbiguousPostPublicationError

__all__ = [
    "ADOPTIONS_DIRNAME",
    "ColdStartAdoptionStorageAmbiguousError",
    "ColdStartAdoptionStorageAmbiguousPostLinkError",
    "ColdStartAdoptionStorageAmbiguousPostPublicationError",
    "ColdStartAdoptionStorageError",
    "MAX_TERMINAL_ADOPTION_RECORD_BYTES",
    "publish_terminal_adoption_record_locked",
    "read_terminal_adoption_record_locked",
]


def _type_sensitive_equal(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_type_sensitive_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_type_sensitive_equal(x, y) for x, y in zip(a, b))
    return bool(a == b)


def _stat_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _open_validated_state_root(state_root: Path) -> int:
    if not isinstance(state_root, Path):
        raise ColdStartAdoptionStorageError("state_root must be a pathlib.Path instance")
    if not state_root.is_absolute():
        raise ColdStartAdoptionStorageError("state_root must be an absolute path")
    state_root_str = str(state_root)
    if state_root_str != os.path.normpath(state_root_str):
        raise ColdStartAdoptionStorageError("state_root must be canonical without redundant segments")
    try:
        st = os.lstat(state_root)
    except FileNotFoundError:
        raise ColdStartAdoptionStorageError("state_root directory does not exist") from None
    except OSError:
        raise ColdStartAdoptionStorageError("Failed to stat state_root") from None

    if stat.S_ISLNK(st.st_mode):
        raise ColdStartAdoptionStorageError("state_root must not be a symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise ColdStartAdoptionStorageError("state_root must be a directory")
    if st.st_uid != os.geteuid():
        raise ColdStartAdoptionStorageError("state_root owner mismatch")
    if (st.st_mode & 0o777) != 0o700:
        raise ColdStartAdoptionStorageError("state_root mode must be 0700")

    try:
        real_root = state_root.resolve(strict=True)
    except OSError:
        raise ColdStartAdoptionStorageError("Failed to resolve state_root") from None
    if real_root != state_root:
        raise ColdStartAdoptionStorageError("state_root contains symlinks")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        root_fd = os.open(str(state_root), flags)
    except OSError:
        raise ColdStartAdoptionStorageError("Failed to open state_root descriptor") from None

    try:
        st_fd = os.fstat(root_fd)
        if (st_fd.st_dev, st_fd.st_ino) != (st.st_dev, st.st_ino):
            raise ColdStartAdoptionStorageError("state_root descriptor identity mismatch")
        if (st_fd.st_mode & 0o777) != 0o700 or not stat.S_ISDIR(st_fd.st_mode) or st_fd.st_uid != os.geteuid():
            raise ColdStartAdoptionStorageError("state_root descriptor metadata invalid")
    except Exception:
        os.close(root_fd)
        raise

    return root_fd


def _open_adoptions_dir(root_fd: int, state_root: Path, create: bool = False) -> int:
    adoptions_path = state_root / ADOPTIONS_DIRNAME
    try:
        st_adoptions = os.stat(ADOPTIONS_DIRNAME, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(ADOPTIONS_DIRNAME, mode=0o700, dir_fd=root_fd)
        except OSError:
            raise ColdStartAdoptionStorageError("Failed to create adoptions directory") from None
        try:
            os.fsync(root_fd)
        except OSError:
            raise ColdStartAdoptionStorageError("Failed to fsync newly created adoptions directory entry") from None
        try:
            st_adoptions = os.stat(ADOPTIONS_DIRNAME, dir_fd=root_fd, follow_symlinks=False)
        except OSError:
            raise ColdStartAdoptionStorageError("Failed to stat newly created adoptions directory") from None
    except OSError:
        raise ColdStartAdoptionStorageError("Failed to stat adoptions directory") from None

    if stat.S_ISLNK(st_adoptions.st_mode):
        raise ColdStartAdoptionStorageError("adoptions directory must not be a symlink")
    if not stat.S_ISDIR(st_adoptions.st_mode):
        raise ColdStartAdoptionStorageError("adoptions must be a directory")
    if st_adoptions.st_uid != os.geteuid():
        raise ColdStartAdoptionStorageError("adoptions directory owner mismatch")
    if (st_adoptions.st_mode & 0o777) != 0o700:
        raise ColdStartAdoptionStorageError("adoptions directory mode must be 0700")

    try:
        real_adoptions = adoptions_path.resolve(strict=True)
    except OSError:
        raise ColdStartAdoptionStorageError("Failed to resolve adoptions directory") from None
    if real_adoptions != adoptions_path:
        raise ColdStartAdoptionStorageError("adoptions directory contains symlinks")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        adoptions_fd = os.open(ADOPTIONS_DIRNAME, flags, dir_fd=root_fd)
    except OSError:
        raise ColdStartAdoptionStorageError("Failed to open adoptions directory descriptor") from None

    try:
        st_fd = os.fstat(adoptions_fd)
        if (st_fd.st_dev, st_fd.st_ino) != (st_adoptions.st_dev, st_adoptions.st_ino):
            raise ColdStartAdoptionStorageError("adoptions directory descriptor identity mismatch")
        if (st_fd.st_mode & 0o777) != 0o700 or not stat.S_ISDIR(st_fd.st_mode) or st_fd.st_uid != os.geteuid():
            raise ColdStartAdoptionStorageError("adoptions directory descriptor metadata invalid")
    except Exception:
        os.close(adoptions_fd)
        raise

    return adoptions_fd


def _load_and_audit_accepted_checkpoint(root_fd: int, state_root: Path) -> tuple[dict[str, Any], str]:
    checkpoint_name = "accepted_checkpoint.json"
    checkpoint_path = state_root / checkpoint_name

    try:
        dirfd_stat_before = os.stat(checkpoint_name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json does not exist") from None
    except OSError:
        raise ColdStartAdoptionStorageError("Failed to stat accepted checkpoint file") from None

    try:
        path_stat_before = os.lstat(checkpoint_path)
    except FileNotFoundError:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json does not exist") from None
    except OSError:
        raise ColdStartAdoptionStorageError("Failed to lstat accepted checkpoint file") from None

    initial_fp = _stat_fingerprint(dirfd_stat_before)
    if _stat_fingerprint(path_stat_before) != initial_fp:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json path and dir_fd stat mismatch")

    if stat.S_ISLNK(dirfd_stat_before.st_mode):
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json must not be a symlink")
    if not stat.S_ISREG(dirfd_stat_before.st_mode):
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json must be a regular file")
    if dirfd_stat_before.st_uid != os.geteuid():
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json owner mismatch")
    if (dirfd_stat_before.st_mode & 0o777) != 0o600:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json mode must be 0600")
    if dirfd_stat_before.st_nlink != 1:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json must be a single-link file")
    if dirfd_stat_before.st_size <= 0:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json is empty")
    if dirfd_stat_before.st_size > MAX_ACCEPTED_CHECKPOINT_BYTES:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json exceeds byte limit")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    file_fd: int | None = None
    read_bytes_1: bytes
    try:
        try:
            file_fd = os.open(checkpoint_name, flags, dir_fd=root_fd)
        except OSError:
            raise ColdStartAdoptionStorageError("Failed to open accepted checkpoint file") from None

        st_fd_before = os.fstat(file_fd)
        if _stat_fingerprint(st_fd_before) != initial_fp:
            raise ColdStartAdoptionStorageError("accepted_checkpoint.json descriptor metadata mismatch before read")

        chunks: list[bytes] = []
        total = 0
        read_limit = MAX_ACCEPTED_CHECKPOINT_BYTES + 1
        while total < read_limit:
            chunk = os.read(file_fd, read_limit - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        read_bytes_1 = b"".join(chunks)

        if len(read_bytes_1) > MAX_ACCEPTED_CHECKPOINT_BYTES:
            raise ColdStartAdoptionStorageError("accepted_checkpoint.json exceeds byte limit")
        if len(read_bytes_1) != dirfd_stat_before.st_size:
            raise ColdStartAdoptionStorageError("accepted_checkpoint.json read size mismatch")

        st_fd_after = os.fstat(file_fd)
        if _stat_fingerprint(st_fd_after) != initial_fp:
            raise ColdStartAdoptionStorageError("accepted_checkpoint.json descriptor metadata changed after read")
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass

    file_fd_2: int | None = None
    read_bytes_2: bytes
    try:
        try:
            file_fd_2 = os.open(checkpoint_name, flags, dir_fd=root_fd)
        except OSError:
            raise ColdStartAdoptionStorageError("Failed to re-open accepted checkpoint file") from None
        st_fd2 = os.fstat(file_fd_2)
        if _stat_fingerprint(st_fd2) != initial_fp:
            raise ColdStartAdoptionStorageError("accepted_checkpoint.json descriptor metadata changed on re-open")
        chunks2: list[bytes] = []
        total2 = 0
        read_limit = MAX_ACCEPTED_CHECKPOINT_BYTES + 1
        while total2 < read_limit:
            chunk2 = os.read(file_fd_2, read_limit - total2)
            if not chunk2:
                break
            chunks2.append(chunk2)
            total2 += len(chunk2)
        read_bytes_2 = b"".join(chunks2)
    finally:
        if file_fd_2 is not None:
            try:
                os.close(file_fd_2)
            except OSError:
                pass

    if read_bytes_1 != read_bytes_2:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json bytes changed between consecutive reads")

    path_stat_after = os.lstat(checkpoint_path)
    if _stat_fingerprint(path_stat_after) != initial_fp:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json path metadata changed after read")

    try:
        text = read_bytes_1.decode("utf-8")
    except UnicodeDecodeError:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json is not valid UTF-8") from None

    try:
        parsed = unique_json(text)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json JSON parse failed") from None

    if not isinstance(parsed, dict):
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json root must be an object")

    try:
        canonical_bytes = serialize_json(parsed)
    except Exception:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json cannot be canonically encoded") from None

    if read_bytes_1 != canonical_bytes:
        raise ColdStartAdoptionStorageError("accepted_checkpoint.json is not canonical JSON bytes")

    try:
        checkpoint_obj = _load_accepted_checkpoint(state_root)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise ColdStartAdoptionStorageError("_load_accepted_checkpoint failed") from None

    if not isinstance(checkpoint_obj, dict):
        raise ColdStartAdoptionStorageError("_load_accepted_checkpoint returned non-dict object")
    if not _type_sensitive_equal(parsed, checkpoint_obj):
        raise ColdStartAdoptionStorageError("accepted checkpoint changed during validation")
    try:
        path_stat_final = os.lstat(checkpoint_path)
    except OSError:
        raise ColdStartAdoptionStorageError("accepted checkpoint disappeared during validation") from None
    if _stat_fingerprint(path_stat_final) != initial_fp:
        raise ColdStartAdoptionStorageError("accepted checkpoint metadata changed during validation")

    checkpoint_sha256 = hashlib.sha256(read_bytes_1).hexdigest()
    return checkpoint_obj, checkpoint_sha256


def publish_terminal_adoption_record_locked(
    state_root: Path,
    new_repository: dict[str, Any],
    old_epoch_basename: str,
    issued_at_utc: str,
) -> Path:
    """Publish a version 1.0 terminal cold-adoption record under exclusive Supervisor lock.

    Precondition: Caller holds the existing exclusive Supervisor lock throughout
    execution. Do not create another lock.
    """
    root_fd = _open_validated_state_root(state_root)
    linked = False
    try:
        if type(new_repository) is not dict:
            raise ColdStartAdoptionStorageError("new_repository must be a dict")
        if type(old_epoch_basename) is not str or not old_epoch_basename:
            raise ColdStartAdoptionStorageError("old_epoch_basename must be a non-empty string")
        if "/" in old_epoch_basename or "\\" in old_epoch_basename or old_epoch_basename in (".", ".."):
            raise ColdStartAdoptionStorageError("old_epoch_basename must be a single directory segment")
        if type(issued_at_utc) is not str:
            raise ColdStartAdoptionStorageError("issued_at_utc must be a string")

        new_repository_snapshot = copy.deepcopy(new_repository)

        old_epoch_dir = state_root / old_epoch_basename
        try:
            terminal_audit_before = inspect_terminal_rescope_epoch(old_epoch_dir)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise ColdStartAdoptionStorageError("Failed to inspect terminal rescope epoch") from None

        if terminal_audit_before.get("epoch_basename") != old_epoch_basename:
            raise ColdStartAdoptionStorageError("terminal audit epoch_basename mismatch")

        _, checkpoint_sha_before = _load_and_audit_accepted_checkpoint(root_fd, state_root)

        try:
            record = build_terminal_adoption_record(
                new_repository=new_repository_snapshot,
                terminal_audit=terminal_audit_before,
                prior_checkpoint_sha256=checkpoint_sha_before,
                issued_at_utc=issued_at_utc,
            )
            canonical_bytes = serialize_terminal_adoption_record(
                record=record,
                new_repository=new_repository_snapshot,
                terminal_audit=terminal_audit_before,
                prior_checkpoint_sha256=checkpoint_sha_before,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except ColdStartAdoptionRecordError:
            raise ColdStartAdoptionStorageError("Failed to build or serialize adoption record") from None

        try:
            target_basename = rescope_epoch_basename(new_repository_snapshot)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise ColdStartAdoptionStorageError("Failed to derive epoch basename from new_repository") from None
        target_filename = f"{target_basename}.json"

        adoptions_fd = _open_adoptions_dir(root_fd, state_root, create=True)
        try:
            target_path = state_root / ADOPTIONS_DIRNAME / target_filename

            target_exists = False
            try:
                os.stat(target_filename, dir_fd=adoptions_fd, follow_symlinks=False)
                target_exists = True
            except FileNotFoundError:
                pass
            except OSError:
                raise ColdStartAdoptionStorageError("Failed to check target file via dir_fd") from None

            if not target_exists:
                try:
                    os.lstat(target_path)
                    target_exists = True
                except FileNotFoundError:
                    pass
                except OSError:
                    raise ColdStartAdoptionStorageError("Failed to lstat target file") from None

            if target_exists:
                raise ColdStartAdoptionStorageError("Adoption record already exists")

            temp_name = f".{target_filename}.tmp.{os.getpid()}"
            temp_created = False
            link_attempted = False
            linked = False

            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            file_fd: int | None = None
            try:
                try:
                    file_fd = os.open(temp_name, flags, 0o600, dir_fd=adoptions_fd)
                except OSError:
                    raise ColdStartAdoptionStorageError("Failed to create temporary file") from None
                temp_created = True

                write_exc: BaseException | None = None
                try:
                    total_written = 0
                    data_len = len(canonical_bytes)
                    while total_written < data_len:
                        written = os.write(file_fd, canonical_bytes[total_written:])
                        if written <= 0:
                            raise ColdStartAdoptionStorageError("Write stalled on temporary file")
                        total_written += written

                    try:
                        os.fchmod(file_fd, 0o600)
                    except OSError:
                        raise ColdStartAdoptionStorageError("Failed to fchmod temporary file") from None

                    st_tmp = os.fstat(file_fd)
                    if st_tmp.st_size != data_len:
                        raise ColdStartAdoptionStorageError("Temporary file size mismatch")
                    if stat.S_IMODE(st_tmp.st_mode) != 0o600:
                        raise ColdStartAdoptionStorageError("Temporary file mode mismatch")
                    if st_tmp.st_uid != os.geteuid():
                        raise ColdStartAdoptionStorageError("Temporary file owner mismatch")

                    try:
                        os.fsync(file_fd)
                    except OSError:
                        raise ColdStartAdoptionStorageError("Failed to fsync temporary file") from None
                except BaseException as exc:
                    write_exc = exc
                    raise
                finally:
                    if file_fd is not None:
                        fd_to_close = file_fd
                        file_fd = None
                        try:
                            os.close(fd_to_close)
                        except OSError:
                            if write_exc is None:
                                raise ColdStartAdoptionStorageError("Failed to close temporary file") from None

                link_attempted = True
                try:
                    os.link(
                        temp_name,
                        target_filename,
                        src_dir_fd=adoptions_fd,
                        dst_dir_fd=adoptions_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    link_attempted = False
                    raise ColdStartAdoptionStorageError("Cannot link adoption record: target already exists") from exc
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as exc:
                    raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
                        "Ambiguous post-link publication failure: link status uncertain"
                    ) from exc

                linked = True

                try:
                    os.fsync(adoptions_fd)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as exc:
                    raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
                        "Ambiguous post-link publication failure: directory fsync after link failed"
                    ) from exc

                try:
                    os.unlink(temp_name, dir_fd=adoptions_fd)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as exc:
                    raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
                        "Ambiguous post-link publication failure: unlink temp file failed"
                    ) from exc

                try:
                    os.fsync(adoptions_fd)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as exc:
                    raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
                        "Ambiguous post-link publication failure: directory fsync after unlink failed"
                    ) from exc
            finally:
                if file_fd is not None:
                    try:
                        os.close(file_fd)
                    except OSError:
                        pass
                if temp_created and not linked and not link_attempted:
                    try:
                        os.unlink(temp_name, dir_fd=adoptions_fd)
                    except OSError:
                        pass
        finally:
            pending_error = sys.exc_info()[1]
            try:
                os.close(adoptions_fd)
            except OSError as exc:
                if pending_error is None:
                    if linked:
                        raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
                            "Ambiguous post-link publication failure: adoptions descriptor close failed"
                        ) from exc
                    raise ColdStartAdoptionStorageError("Adoptions descriptor close failed") from exc
    finally:
        pending_error = sys.exc_info()[1]
        try:
            os.close(root_fd)
        except OSError as exc:
            if pending_error is None:
                if linked:
                    raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
                        "Ambiguous post-link publication failure: state_root descriptor close failed"
                    ) from exc
                raise ColdStartAdoptionStorageError("State_root descriptor close failed") from exc

    try:
        terminal_audit_after = inspect_terminal_rescope_epoch(old_epoch_dir)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
            "Ambiguous post-link publication failure: old terminal audit failed"
        ) from exc
    if not _type_sensitive_equal(terminal_audit_before, terminal_audit_after):
        raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
            "Ambiguous post-link publication failure: old terminal audit drifted after publication"
        )

    check_root_fd = _open_validated_state_root(state_root)
    try:
        _, checkpoint_sha_after = _load_and_audit_accepted_checkpoint(check_root_fd, state_root)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
            "Ambiguous post-link publication failure: accepted checkpoint revalidation failed"
        ) from exc
    finally:
        pending_error = sys.exc_info()[1]
        try:
            os.close(check_root_fd)
        except OSError as exc:
            if pending_error is None:
                raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
                    "Ambiguous post-link publication failure: revalidation descriptor close failed"
                ) from exc

    if checkpoint_sha_after != checkpoint_sha_before:
        raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
            "Ambiguous post-link publication failure: accepted checkpoint SHA drifted after publication"
        )

    if not _type_sensitive_equal(new_repository, new_repository_snapshot):
        raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
            "Ambiguous post-link publication failure: caller new_repository mutated during publication"
        )

    try:
        readback = read_terminal_adoption_record_locked(state_root, new_repository_snapshot)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
            "Ambiguous post-link publication failure: readback failed"
        ) from exc

    if readback is None:
        raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
            "Ambiguous post-link publication failure: readback returned None"
        )
    if not _type_sensitive_equal(readback, record):
        raise ColdStartAdoptionStorageAmbiguousPostPublicationError(
            "Ambiguous post-link publication failure: readback record does not match published record"
        )

    return target_path


def read_terminal_adoption_record_locked(
    state_root: Path,
    new_repository: dict[str, Any],
) -> dict[str, Any] | None:
    """Read and strictly validate terminal cold-adoption record under exclusive Supervisor lock.

    Precondition: Caller holds the existing exclusive Supervisor lock throughout
    execution. Do not create another lock.
    """
    root_fd = _open_validated_state_root(state_root)
    try:
        if type(new_repository) is not dict:
            raise ColdStartAdoptionStorageError("new_repository must be a dict")

        try:
            target_basename = rescope_epoch_basename(new_repository)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise ColdStartAdoptionStorageError("Failed to derive epoch basename") from None
        target_filename = f"{target_basename}.json"

        try:
            adoptions_fd = _open_adoptions_dir(root_fd, state_root, create=False)
        except FileNotFoundError:
            return None

        try:
            target_path = state_root / ADOPTIONS_DIRNAME / target_filename
            try:
                st_target = os.stat(target_filename, dir_fd=adoptions_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None
            except OSError:
                raise ColdStartAdoptionStorageError("Failed to stat adoption record via dir_fd") from None

            try:
                path_lstat = os.lstat(target_path)
            except FileNotFoundError:
                raise ColdStartAdoptionStorageError("Adoption record disappeared after stat") from None
            except OSError:
                raise ColdStartAdoptionStorageError("Failed to lstat adoption record") from None

            initial_fp = _stat_fingerprint(st_target)
            if _stat_fingerprint(path_lstat) != initial_fp:
                raise ColdStartAdoptionStorageError("Adoption record dir_fd and path stat mismatch")

            if stat.S_ISLNK(st_target.st_mode) or not stat.S_ISREG(st_target.st_mode):
                raise ColdStartAdoptionStorageError("Adoption record is not a regular file")
            if st_target.st_uid != os.geteuid():
                raise ColdStartAdoptionStorageError("Adoption record owner mismatch")
            if stat.S_IMODE(st_target.st_mode) != 0o600:
                raise ColdStartAdoptionStorageError("Adoption record mode must be 0600")
            if st_target.st_nlink != 1:
                raise ColdStartAdoptionStorageError("Adoption record has invalid link count")
            if st_target.st_size <= 0:
                raise ColdStartAdoptionStorageError("Adoption record is empty")
            if st_target.st_size > MAX_TERMINAL_ADOPTION_RECORD_BYTES:
                raise ColdStartAdoptionStorageError("Adoption record exceeds byte limit")

            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            file_fd: int | None = None
            try:
                try:
                    file_fd = os.open(target_filename, flags, dir_fd=adoptions_fd)
                except FileNotFoundError:
                    raise ColdStartAdoptionStorageError("Adoption record disappeared before open") from None
                except OSError:
                    raise ColdStartAdoptionStorageError("Failed to open adoption record file") from None

                st_fd_before = os.fstat(file_fd)
                if _stat_fingerprint(st_fd_before) != initial_fp:
                    raise ColdStartAdoptionStorageError("Adoption record descriptor metadata mismatch before read")

                chunks: list[bytes] = []
                total_read = 0
                read_limit = MAX_TERMINAL_ADOPTION_RECORD_BYTES + 1
                while total_read < read_limit:
                    chunk = os.read(file_fd, read_limit - total_read)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_read += len(chunk)

                data = b"".join(chunks)
                if len(data) > MAX_TERMINAL_ADOPTION_RECORD_BYTES:
                    raise ColdStartAdoptionStorageError("Adoption record exceeds byte limit")
                if len(data) != st_target.st_size:
                    raise ColdStartAdoptionStorageError("Adoption record read size mismatch")

                st_fd_after = os.fstat(file_fd)
                if _stat_fingerprint(st_fd_after) != initial_fp:
                    raise ColdStartAdoptionStorageError("Adoption record descriptor metadata changed after read")

                dirfd_stat_after = os.stat(target_filename, dir_fd=adoptions_fd, follow_symlinks=False)
                if _stat_fingerprint(dirfd_stat_after) != initial_fp:
                    raise ColdStartAdoptionStorageError("Adoption record dir_fd stat changed after read")

                path_lstat_after = os.lstat(target_path)
                if _stat_fingerprint(path_lstat_after) != initial_fp:
                    raise ColdStartAdoptionStorageError("Adoption record path stat changed after read")
            finally:
                if file_fd is not None:
                    pending_error = sys.exc_info()[1]
                    try:
                        os.close(file_fd)
                    except OSError as exc:
                        if pending_error is None:
                            raise ColdStartAdoptionStorageError("Adoption record descriptor close failed") from exc
        finally:
            pending_error = sys.exc_info()[1]
            try:
                os.close(adoptions_fd)
            except OSError as exc:
                if pending_error is None:
                    raise ColdStartAdoptionStorageError("Adoptions descriptor close failed during read") from exc
    finally:
        pending_error = sys.exc_info()[1]
        try:
            os.close(root_fd)
        except OSError as exc:
            if pending_error is None:
                raise ColdStartAdoptionStorageError("State_root descriptor close failed during read") from exc

    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ColdStartAdoptionStorageError("Adoption record is not valid UTF-8") from None

    try:
        raw_payload = unique_json(decoded)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise ColdStartAdoptionStorageError("Adoption record is not valid unique-key JSON") from None

    if not isinstance(raw_payload, dict):
        raise ColdStartAdoptionStorageError("Adoption record must be a JSON object")

    if "issuer" in raw_payload or "approved" in raw_payload:
        raise ColdStartAdoptionStorageError("Adoption record contains disallowed authority fields")

    old_epoch_basename = raw_payload.get("old_epoch_basename")
    if type(old_epoch_basename) is not str or not old_epoch_basename:
        raise ColdStartAdoptionStorageError("Adoption record missing old_epoch_basename")
    if "/" in old_epoch_basename or "\\" in old_epoch_basename or old_epoch_basename in (".", ".."):
        raise ColdStartAdoptionStorageError("Adoption record old_epoch_basename is not a single directory segment")

    old_epoch_dir = state_root / old_epoch_basename
    try:
        terminal_audit = inspect_terminal_rescope_epoch(old_epoch_dir)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise ColdStartAdoptionStorageError("Failed to inspect terminal rescope epoch") from None

    check_root_fd = _open_validated_state_root(state_root)
    try:
        _, checkpoint_sha = _load_and_audit_accepted_checkpoint(check_root_fd, state_root)
    finally:
        pending_error = sys.exc_info()[1]
        try:
            os.close(check_root_fd)
        except OSError as exc:
            if pending_error is None:
                raise ColdStartAdoptionStorageError("Checkpoint descriptor close failed during read") from exc

    try:
        validated = parse_terminal_adoption_record(
            raw=data,
            new_repository=new_repository,
            terminal_audit=terminal_audit,
            prior_checkpoint_sha256=checkpoint_sha,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except ColdStartAdoptionRecordError:
        raise ColdStartAdoptionStorageError("Adoption record validation failed") from None

    return copy.deepcopy(validated)
