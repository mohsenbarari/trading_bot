"""Terminal continuation storage for two-site quota continuation receipts."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Iterator
import uuid

try:
    from scripts.two_site_provider_broker import BrokerError, STATE_REL, canonical_root
except ImportError:
    from two_site_provider_broker import BrokerError, STATE_REL, canonical_root  # type: ignore

try:
    from scripts.two_site_quota_continuation_terminal_record import (
        MAX_TERMINAL_CONTINUATION_RECORD_BYTES,
        build_terminal_continuation_record,
        validate_terminal_continuation_record,
    )
except ImportError:
    from two_site_quota_continuation_terminal_record import (  # type: ignore
        MAX_TERMINAL_CONTINUATION_RECORD_BYTES,
        build_terminal_continuation_record,
        validate_terminal_continuation_record,
    )

try:
    from scripts.two_site_quota_resume_submission_intent import (
        read_quota_resume_submission_intent,
    )
except ImportError:
    from two_site_quota_resume_submission_intent import (  # type: ignore
        read_quota_resume_submission_intent,
    )

TERMINAL_DIR_NAME = "terminal-continuation"
MAX_TERMINAL_RECORDS_CAPACITY = 64
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_RECEIPT_FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.json$")
_OPEN_DIR_FLAGS = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_OPEN_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


class TerminalContinuationStorageError(BrokerError):
    """Raised when terminal continuation storage publish, read, or validation fails."""


__all__ = [
    "MAX_TERMINAL_CONTINUATION_RECORD_BYTES",
    "TerminalContinuationStorageError",
    "publish_terminal_continuation_record",
    "read_terminal_continuation_record",
]


def _stat_fp(st: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_uid,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def _validate_receipt_stat(st: os.stat_result, label: str) -> None:
    if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise TerminalContinuationStorageError(f"{label} is not a regular file")
    if st.st_uid != os.geteuid():
        raise TerminalContinuationStorageError(f"{label} owner mismatch")
    if stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1:
        raise TerminalContinuationStorageError(f"{label} invalid mode or link count")
    if not (0 <= st.st_size <= MAX_TERMINAL_CONTINUATION_RECORD_BYTES):
        raise TerminalContinuationStorageError(f"{label} size out of bounds")


def _canonical_bytes(obj: Any) -> bytes:
    try:
        return json.dumps(
            obj,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except Exception:
        raise TerminalContinuationStorageError("cannot serialize to canonical JSON") from None


def _parse_canonical_receipt_bytes(raw_bytes: bytes) -> dict[str, Any]:
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise TerminalContinuationStorageError("receipt is not valid UTF-8") from None

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        res: dict[str, Any] = {}
        for k, v in pairs:
            if k in res:
                raise TerminalContinuationStorageError("duplicate JSON key in receipt")
            res[k] = v
        return res

    try:
        payload = json.loads(decoded, object_pairs_hook=_pairs)
    except TerminalContinuationStorageError:
        raise
    except Exception:
        raise TerminalContinuationStorageError("receipt is not valid JSON") from None
    if type(payload) is not dict or _canonical_bytes(payload) != raw_bytes:
        raise TerminalContinuationStorageError("receipt payload must be canonical unique JSON dict")
    return payload


@contextmanager
def _open_terminal_directory(
    root: Path, *, create_if_absent: bool = False
) -> Iterator[tuple[Path, int, Callable[[], None]]]:
    try:
        c_root = canonical_root(root)
    except BrokerError:
        raise TerminalContinuationStorageError("invalid broker root") from None
    open_fds: list[int] = []
    records: list[tuple[Path, int, int, int, int, int]] = []
    try:
        try:
            root_stat = os.lstat(c_root)
            root_fd = os.open(c_root, _OPEN_DIR_FLAGS)
        except OSError as exc:
            raise TerminalContinuationStorageError(f"cannot open root: errno={exc.errno}") from None
        open_fds.append(root_fd)
        try:
            root_fstat = os.fstat(root_fd)
        except OSError as exc:
            raise TerminalContinuationStorageError(f"cannot fstat root: errno={exc.errno}") from None
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or stat.S_ISLNK(root_stat.st_mode)
            or root_stat.st_uid != os.geteuid()
            or not stat.S_ISDIR(root_fstat.st_mode)
            or stat.S_ISLNK(root_fstat.st_mode)
            or root_fstat.st_uid != os.geteuid()
            or (root_stat.st_dev, root_stat.st_ino, root_stat.st_uid, root_stat.st_mode)
            != (root_fstat.st_dev, root_fstat.st_ino, root_fstat.st_uid, root_fstat.st_mode)
        ):
            raise TerminalContinuationStorageError("root directory invalid, descriptor mismatch, or owner mismatch")
        records.append((c_root, root_fd, root_fstat.st_dev, root_fstat.st_ino, root_fstat.st_uid, root_fstat.st_mode))
        current_fd, current_path = root_fd, c_root
        for comp in list(STATE_REL.parts) + [TERMINAL_DIR_NAME]:
            is_broker = (comp == "provider-broker")
            is_term = (comp == TERMINAL_DIR_NAME)
            child_path = current_path / comp
            if is_term and create_if_absent:
                try:
                    os.stat(comp, dir_fd=current_fd, follow_symlinks=False)
                except FileNotFoundError:
                    try:
                        os.mkdir(comp, 0o700, dir_fd=current_fd)
                    except OSError as exc:
                        raise TerminalContinuationStorageError(f"cannot create {comp}: errno={exc.errno}") from None
                except OSError as exc:
                    raise TerminalContinuationStorageError(f"cannot inspect {comp}: errno={exc.errno}") from None
            try:
                p_stat = os.lstat(child_path)
            except OSError as exc:
                raise TerminalContinuationStorageError(f"cannot lstat {comp}: errno={exc.errno}") from None
            if not stat.S_ISDIR(p_stat.st_mode) or stat.S_ISLNK(p_stat.st_mode):
                raise TerminalContinuationStorageError(f"{comp} is not a directory or is a symlink")
            if p_stat.st_uid != os.geteuid():
                raise TerminalContinuationStorageError(f"{comp} owner mismatch")
            try:
                child_fd = os.open(comp, _OPEN_DIR_FLAGS, dir_fd=current_fd)
            except OSError as exc:
                raise TerminalContinuationStorageError(f"cannot open {comp}: errno={exc.errno}") from None
            open_fds.append(child_fd)
            try:
                f_stat = os.fstat(child_fd)
            except OSError as exc:
                raise TerminalContinuationStorageError(f"cannot fstat {comp}: errno={exc.errno}") from None
            if not stat.S_ISDIR(f_stat.st_mode) or stat.S_ISLNK(f_stat.st_mode):
                raise TerminalContinuationStorageError(f"{comp} descriptor is not a directory or is a symlink")
            if (p_stat.st_dev, p_stat.st_ino, p_stat.st_uid, p_stat.st_mode) != (
                f_stat.st_dev, f_stat.st_ino, f_stat.st_uid, f_stat.st_mode
            ):
                raise TerminalContinuationStorageError(f"{comp} descriptor identity mismatch")
            if f_stat.st_uid != os.geteuid():
                raise TerminalContinuationStorageError(f"{comp} owner mismatch")
            if (is_broker or is_term) and stat.S_IMODE(f_stat.st_mode) != 0o700:
                raise TerminalContinuationStorageError(f"{comp} mode must be 0700")
            records.append((child_path, child_fd, f_stat.st_dev, f_stat.st_ino, f_stat.st_uid, f_stat.st_mode))
            current_fd, current_path = child_fd, child_path

        term_path, term_fd = current_path, current_fd

        def verify_identity() -> None:
            for path, fd, exp_dev, exp_ino, exp_uid, exp_mode in records:
                lbl = path.name or "root"
                try:
                    st = os.lstat(path)
                    fst = os.fstat(fd)
                except OSError as exc:
                    raise TerminalContinuationStorageError(
                        f"directory verification failed for {lbl}: errno={exc.errno}"
                    ) from None
                if stat.S_ISLNK(st.st_mode) or stat.S_ISLNK(fst.st_mode):
                    raise TerminalContinuationStorageError(f"directory path component is a symlink: {lbl}")
                if not stat.S_ISDIR(st.st_mode) or not stat.S_ISDIR(fst.st_mode):
                    raise TerminalContinuationStorageError(f"directory path component is not a directory: {lbl}")
                if (st.st_dev, st.st_ino, st.st_uid, st.st_mode) != (exp_dev, exp_ino, exp_uid, exp_mode):
                    raise TerminalContinuationStorageError(f"directory path identity changed: {lbl}")
                if (fst.st_dev, fst.st_ino, fst.st_uid, fst.st_mode) != (exp_dev, exp_ino, exp_uid, exp_mode):
                    raise TerminalContinuationStorageError(f"directory descriptor identity changed: {lbl}")

        yield term_path, term_fd, verify_identity
    finally:
        for fd in reversed(open_fds):
            try:
                os.close(fd)
            except OSError:
                pass


def read_terminal_continuation_record(
    root: Path, request_id: str, *, expected_request: dict | None = None
) -> dict:
    if not isinstance(root, Path):
        raise TerminalContinuationStorageError("root must be a Path")
    if type(request_id) is not str or not _REQUEST_ID_RE.fullmatch(request_id):
        raise TerminalContinuationStorageError("request_id must be 32 lowercase hex")
    if expected_request is not None and type(expected_request) is not dict:
        raise TerminalContinuationStorageError("expected_request must be dict or None")
    target_name = f"{request_id}.json"
    with _open_terminal_directory(root, create_if_absent=False) as (term_path, term_fd, verify_identity):
        verify_identity()
        target_path = term_path / target_name
        try:
            lstat_before = os.lstat(target_path)
            dirfd_stat_before = os.stat(target_name, dir_fd=term_fd, follow_symlinks=False)
        except OSError as exc:
            raise TerminalContinuationStorageError(f"cannot stat receipt: errno={exc.errno}") from None
        _validate_receipt_stat(lstat_before, "receipt path before open")
        _validate_receipt_stat(dirfd_stat_before, "receipt via dir_fd before open")
        initial_fp = _stat_fp(lstat_before)
        if _stat_fp(dirfd_stat_before) != initial_fp:
            raise TerminalContinuationStorageError("receipt dir_fd stat mismatch before open")
        file_fd: int | None = None
        try:
            try:
                file_fd = os.open(target_name, _OPEN_FILE_FLAGS, dir_fd=term_fd)
            except OSError as exc:
                raise TerminalContinuationStorageError(f"cannot open receipt file: errno={exc.errno}") from None
            fstat_before = os.fstat(file_fd)
            _validate_receipt_stat(fstat_before, "receipt descriptor before read")
            if _stat_fp(fstat_before) != initial_fp:
                raise TerminalContinuationStorageError("receipt descriptor metadata mismatch before read")
            read_limit = MAX_TERMINAL_CONTINUATION_RECORD_BYTES + 1
            chunks: list[bytes] = []
            total_read = 0
            while total_read < read_limit:
                try:
                    chunk = os.read(file_fd, read_limit - total_read)
                except OSError as exc:
                    raise TerminalContinuationStorageError(f"cannot read receipt file: errno={exc.errno}") from None
                if not chunk:
                    break
                chunks.append(chunk)
                total_read += len(chunk)
            data = b"".join(chunks)
            if len(data) > MAX_TERMINAL_CONTINUATION_RECORD_BYTES or len(data) != initial_fp[5]:
                raise TerminalContinuationStorageError("receipt size mismatch or exceeds limit")
            fstat_after = os.fstat(file_fd)
            dirfd_stat_after = os.stat(target_name, dir_fd=term_fd, follow_symlinks=False)
            lstat_after = os.lstat(target_path)
            for s, lbl in ((fstat_after, "fd after"), (dirfd_stat_after, "dir_fd after"), (lstat_after, "path after")):
                _validate_receipt_stat(s, f"receipt {lbl}")
                if _stat_fp(s) != initial_fp:
                    raise TerminalContinuationStorageError(f"receipt {lbl} metadata changed")
            verify_identity()
        finally:
            if file_fd is not None:
                try:
                    os.close(file_fd)
                except OSError:
                    pass
    payload = _parse_canonical_receipt_bytes(data)
    if payload.get("request_id") != request_id:
        raise TerminalContinuationStorageError("record request_id mismatch")
    rec_req = payload.get("request")
    if type(rec_req) is not dict or rec_req.get("request_id") != request_id:
        raise TerminalContinuationStorageError("record inner request invalid")
    orig_cid = rec_req.get("original_controller_run_id")
    if type(orig_cid) is not str or not orig_cid:
        raise TerminalContinuationStorageError("record request missing original_controller_run_id")
    try:
        actual_intent = read_quota_resume_submission_intent(root, orig_cid, rec_req)
    except Exception:
        raise TerminalContinuationStorageError("cannot read persisted submission intent") from None
    try:
        validated = validate_terminal_continuation_record(
            payload,
            expected_intent=actual_intent,
            expected_request=expected_request if expected_request is not None else rec_req,
        )
    except Exception:
        raise TerminalContinuationStorageError("terminal continuation record validation failed") from None
    if expected_request is not None and _canonical_bytes(validated["request"]) != _canonical_bytes(expected_request):
        raise TerminalContinuationStorageError("record request does not match expected_request")
    return validated


def publish_terminal_continuation_record(root: Path, request: dict, response: dict) -> dict:
    if not isinstance(root, Path):
        raise TerminalContinuationStorageError("root must be a Path")
    if type(request) is not dict or type(response) is not dict:
        raise TerminalContinuationStorageError("request and response must be dicts")
    request_id = request.get("request_id")
    if type(request_id) is not str or not _REQUEST_ID_RE.fullmatch(request_id):
        raise TerminalContinuationStorageError("request request_id must be 32 lowercase hex")
    orig_cid = request.get("original_controller_run_id")
    if type(orig_cid) is not str or not orig_cid:
        raise TerminalContinuationStorageError("request missing original_controller_run_id")
    try:
        persisted_intent = read_quota_resume_submission_intent(root, orig_cid, request)
    except Exception:
        raise TerminalContinuationStorageError("cannot read persisted submission intent") from None
    try:
        record = build_terminal_continuation_record(request, response, persisted_intent)
    except Exception:
        raise TerminalContinuationStorageError("cannot build terminal continuation record") from None
    canonical_bytes = _canonical_bytes(record)
    if len(canonical_bytes) > MAX_TERMINAL_CONTINUATION_RECORD_BYTES:
        raise TerminalContinuationStorageError("serialized record exceeds byte limit")
    target_name = f"{request_id}.json"
    temp_name = f".{request_id}.json.tmp.{uuid.uuid4().hex}"
    temp_created = False
    linked = False
    try:
        with _open_terminal_directory(root, create_if_absent=True) as (term_path, term_fd, verify_identity):
            try:
                verify_identity()
                try:
                    entries = os.listdir(term_fd)
                except OSError as exc:
                    raise TerminalContinuationStorageError(f"cannot scan terminal directory: errno={exc.errno}") from None
                if len(entries) >= MAX_TERMINAL_RECORDS_CAPACITY:
                    raise TerminalContinuationStorageError("terminal directory capacity limit reached")
                for name in entries:
                    if not _RECEIPT_FILENAME_RE.fullmatch(name):
                        raise TerminalContinuationStorageError("unexpected entry in terminal directory")
                    if name == target_name:
                        raise TerminalContinuationStorageError("terminal record already exists")
                target_path = term_path / target_name
                for chk, fd_arg in ((target_name, term_fd), (target_path, None)):
                    try:
                        if fd_arg is not None:
                            os.stat(chk, dir_fd=fd_arg, follow_symlinks=False)
                        else:
                            os.lstat(chk)
                        raise TerminalContinuationStorageError("target already exists")
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        raise TerminalContinuationStorageError(f"cannot inspect target: errno={exc.errno}") from None
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                try:
                    temp_fd = os.open(temp_name, flags, 0o600, dir_fd=term_fd)
                except OSError as exc:
                    raise TerminalContinuationStorageError(f"cannot create temp file: errno={exc.errno}") from None
                temp_created = True
                try:
                    total_written = 0
                    data_len = len(canonical_bytes)
                    while total_written < data_len:
                        try:
                            written = os.write(temp_fd, canonical_bytes[total_written:])
                        except OSError as exc:
                            raise TerminalContinuationStorageError(f"cannot write temp file: errno={exc.errno}") from None
                        if written <= 0:
                            raise TerminalContinuationStorageError("write made no progress")
                        total_written += written
                    try:
                        os.fchmod(temp_fd, 0o600)
                    except OSError as exc:
                        raise TerminalContinuationStorageError(f"cannot fchmod temp file: errno={exc.errno}") from None
                    try:
                        temp_stat = os.fstat(temp_fd)
                    except OSError as exc:
                        raise TerminalContinuationStorageError(f"cannot fstat temp file: errno={exc.errno}") from None
                    _validate_receipt_stat(temp_stat, "temp file")
                    if temp_stat.st_size != data_len:
                        raise TerminalContinuationStorageError("temp file size mismatch")
                    try:
                        os.fsync(temp_fd)
                    except OSError as exc:
                        raise TerminalContinuationStorageError(f"cannot fsync temp file: errno={exc.errno}") from None
                finally:
                    try:
                        os.close(temp_fd)
                    except OSError:
                        pass
                try:
                    os.link(temp_name, target_name, src_dir_fd=term_fd, dst_dir_fd=term_fd, follow_symlinks=False)
                except FileExistsError:
                    raise TerminalContinuationStorageError("target appeared during publication") from None
                except OSError as exc:
                    raise TerminalContinuationStorageError(f"cannot link target: errno={exc.errno}") from None
                linked = True
                try:
                    os.unlink(temp_name, dir_fd=term_fd)
                except OSError as exc:
                    raise TerminalContinuationStorageError(
                        f"ambiguous post-link publication failure: cannot unlink temp: errno={exc.errno}"
                    ) from None
                try:
                    os.fsync(term_fd)
                except OSError as exc:
                    raise TerminalContinuationStorageError(
                        f"ambiguous post-link publication failure: cannot fsync directory: errno={exc.errno}"
                    ) from None
                try:
                    verify_identity()
                except Exception:
                    raise TerminalContinuationStorageError(
                        "ambiguous post-link publication failure: directory identity verification failed"
                    ) from None
            finally:
                if temp_created and not linked:
                    try:
                        os.unlink(temp_name, dir_fd=term_fd)
                    except OSError:
                        pass
    except Exception as exc:
        if linked:
            if isinstance(exc, TerminalContinuationStorageError) and str(exc).startswith(
                "ambiguous post-link publication failure"
            ):
                raise
            raise TerminalContinuationStorageError(
                "ambiguous post-link publication failure: storage error"
            ) from None
        if isinstance(exc, TerminalContinuationStorageError):
            raise
        raise TerminalContinuationStorageError("publication failed: storage error") from None
    try:
        readback_record = read_terminal_continuation_record(root, request_id, expected_request=request)
    except Exception:
        raise TerminalContinuationStorageError(
            "ambiguous post-link publication failure: readback failed"
        ) from None
    try:
        readback_bytes = _canonical_bytes(readback_record)
    except Exception:
        raise TerminalContinuationStorageError(
            "ambiguous post-link publication failure: readback serialization failed"
        ) from None
    if readback_bytes != canonical_bytes:
        raise TerminalContinuationStorageError(
            "ambiguous post-link publication failure: readback record mismatch"
        )
    return readback_record
