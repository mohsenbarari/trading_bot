"""Read-only verification of pending rescope intent markers."""

from __future__ import annotations

from copy import deepcopy
import errno
import os
from pathlib import Path
import stat
import sys

from scripts.two_site_codex_autopilot import (
    AutopilotError,
    RESCOPE_INTENT_FILENAMES,
    _open_private_rescope_state_dir,
    _recheck_final_root,
    load_latest_rescope_receipt,
    rescope_epoch_basename,
    validate_accepted_repository,
    validate_repository,
)
from scripts.two_site_provider_broker import BrokerError, canonical_root

__all__ = [
    "WaitIntentError",
    "read_pending_rescope_intent",
]

_LEAF_METADATA_FIELDS: tuple[str, ...] = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_uid",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


class WaitIntentError(ValueError):
    """Raised when pending rescope intent cannot be safely verified."""


def _check_future_intents_absent(epoch_fd: int, attempt: int) -> None:
    for fut_attempt in range(attempt + 1, 4):
        fut_leaf = RESCOPE_INTENT_FILENAMES[fut_attempt]
        try:
            os.stat(fut_leaf, dir_fd=epoch_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                continue
            raise WaitIntentError(
                f"error verifying absence of future intent {fut_leaf!r}: {exc}"
            ) from exc
        raise WaitIntentError(f"future intent marker {fut_leaf!r} must be absent")


def _read_and_verify_marker(epoch_dir: Path, attempt: int) -> None:
    leaf_name = RESCOPE_INTENT_FILENAMES[attempt]
    payload = f"{leaf_name}\n".encode("ascii")

    epoch_fd: int | None = None
    file_fd: int | None = None
    primary_exc: BaseException | None = None

    try:
        epoch_fd = _open_private_rescope_state_dir(epoch_dir)

        _check_future_intents_absent(epoch_fd, attempt)

        try:
            pre_st = os.stat(leaf_name, dir_fd=epoch_fd, follow_symlinks=False)
        except OSError as exc:
            raise WaitIntentError(
                f"cannot pre-stat intent leaf {leaf_name!r}: {exc}"
            ) from exc

        if not stat.S_ISREG(pre_st.st_mode) or stat.S_ISLNK(pre_st.st_mode):
            raise WaitIntentError(
                f"intent leaf {leaf_name!r} must be a regular non-symlink file"
            )
        if pre_st.st_uid != os.geteuid():
            raise WaitIntentError(
                f"intent leaf {leaf_name!r} owner must match process euid"
            )
        if (pre_st.st_mode & 0o777) != 0o600:
            raise WaitIntentError(
                f"intent leaf {leaf_name!r} mode must be 0600"
            )
        if pre_st.st_nlink != 1:
            raise WaitIntentError(
                f"intent leaf {leaf_name!r} link count must be 1"
            )
        if pre_st.st_size != len(payload):
            raise WaitIntentError(
                f"intent leaf {leaf_name!r} size must be {len(payload)}"
            )

        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            file_fd = os.open(leaf_name, flags, dir_fd=epoch_fd)
        except OSError as exc:
            raise WaitIntentError(
                f"cannot open intent leaf {leaf_name!r}: {exc}"
            ) from exc

        try:
            fd_st = os.fstat(file_fd)
        except OSError as exc:
            raise WaitIntentError(
                f"cannot fstat intent leaf {leaf_name!r}: {exc}"
            ) from exc

        if not stat.S_ISREG(fd_st.st_mode) or stat.S_ISLNK(fd_st.st_mode):
            raise WaitIntentError(
                f"intent fd {leaf_name!r} must be a regular non-symlink file"
            )
        if fd_st.st_uid != os.geteuid():
            raise WaitIntentError(
                f"intent fd {leaf_name!r} owner must match process euid"
            )
        if (fd_st.st_mode & 0o777) != 0o600:
            raise WaitIntentError(
                f"intent fd {leaf_name!r} mode must be 0600"
            )
        if fd_st.st_nlink != 1:
            raise WaitIntentError(
                f"intent fd {leaf_name!r} link count must be 1"
            )
        if fd_st.st_size != len(payload):
            raise WaitIntentError(
                f"intent fd {leaf_name!r} size must be {len(payload)}"
            )
        if (fd_st.st_dev, fd_st.st_ino) != (pre_st.st_dev, pre_st.st_ino):
            raise WaitIntentError(
                f"intent fd {leaf_name!r} inode mismatch with pre-stat"
            )

        try:
            data = os.read(file_fd, len(payload) + 1)
        except OSError as exc:
            raise WaitIntentError(
                f"cannot read intent leaf {leaf_name!r}: {exc}"
            ) from exc

        if data != payload:
            raise WaitIntentError(
                f"intent leaf {leaf_name!r} payload mismatch"
            )

        try:
            post_st = os.stat(leaf_name, dir_fd=epoch_fd, follow_symlinks=False)
        except OSError as exc:
            raise WaitIntentError(
                f"cannot post-stat intent leaf {leaf_name!r}: {exc}"
            ) from exc

        for field in _LEAF_METADATA_FIELDS:
            if getattr(post_st, field) != getattr(pre_st, field):
                raise WaitIntentError(
                    f"intent leaf {leaf_name!r} {field} modified during read"
                )

        try:
            post_fd_st = os.fstat(file_fd)
        except OSError as exc:
            raise WaitIntentError(
                f"cannot post-fstat intent fd {leaf_name!r}: {exc}"
            ) from exc

        for field in _LEAF_METADATA_FIELDS:
            if getattr(post_fd_st, field) != getattr(pre_st, field):
                raise WaitIntentError(
                    f"intent fd {leaf_name!r} {field} modified during read"
                )

        _check_future_intents_absent(epoch_fd, attempt)

        _recheck_final_root(epoch_dir, epoch_fd)
    except BaseException as exc:
        primary_exc = exc
        raise
    finally:
        active_exc_type, active_exc, _ = sys.exc_info()
        close_errors: list[OSError] = []
        interrupt_exc: BaseException | None = None

        if file_fd is not None:
            try:
                os.close(file_fd)
            except (KeyboardInterrupt, SystemExit) as exc:
                interrupt_exc = exc
            except OSError as exc:
                close_errors.append(exc)

        if epoch_fd is not None:
            try:
                os.close(epoch_fd)
            except (KeyboardInterrupt, SystemExit) as exc:
                if interrupt_exc is None:
                    interrupt_exc = exc
            except OSError as exc:
                close_errors.append(exc)

        is_interrupt = (
            (active_exc_type is not None and issubclass(active_exc_type, (KeyboardInterrupt, SystemExit)))
            or isinstance(primary_exc, (KeyboardInterrupt, SystemExit))
            or interrupt_exc is not None
        )
        if is_interrupt:
            if interrupt_exc is not None:
                raise interrupt_exc
        elif close_errors:
            first_close_err = close_errors[0]
            close_exc = WaitIntentError(f"error closing descriptor: {first_close_err}")
            if primary_exc is not None and isinstance(primary_exc, Exception):
                close_exc.__cause__ = primary_exc
            elif active_exc is not None and isinstance(active_exc, Exception):
                close_exc.__cause__ = active_exc
            else:
                close_exc.__cause__ = first_close_err
            raise close_exc


def read_pending_rescope_intent(root: Path) -> tuple[str, int, dict[str, object]]:
    """Verify and read the current pending rescope intent marker in a read-only manner."""
    if not isinstance(root, Path):
        raise WaitIntentError("root must be a Path")

    try:
        norm_root = canonical_root(root)
        repo = validate_repository(norm_root, require_clean=True)
        norm_repo = validate_accepted_repository(repo)
        epoch = rescope_epoch_basename(norm_repo)

        epoch_dir = norm_root / ".local" / "two-site-refactor" / "autopilot" / epoch
        try:
            epoch_st = epoch_dir.stat(follow_symlinks=False)
        except OSError as exc:
            raise WaitIntentError(
                f"epoch directory does not exist or inaccessible: {exc}"
            ) from exc
        if stat.S_ISLNK(epoch_st.st_mode) or not stat.S_ISDIR(epoch_st.st_mode):
            raise WaitIntentError(
                f"epoch directory is not a regular directory: {epoch_dir}"
            )

        latest_receipt = load_latest_rescope_receipt(epoch_dir, norm_repo)
        if latest_receipt is None:
            attempt = 1
        else:
            if not isinstance(latest_receipt, dict):
                raise WaitIntentError("rescope receipt must be a dict")
            terminal = latest_receipt.get("terminal")
            if terminal is not False:
                raise WaitIntentError(
                    f"latest receipt terminal flag must be False, got {terminal!r}"
                )
            prev_attempt = latest_receipt.get("attempt")
            if (
                type(prev_attempt) is not int
                or isinstance(prev_attempt, bool)
                or prev_attempt not in (1, 2)
            ):
                raise WaitIntentError(
                    f"latest receipt attempt must be exact integer 1 or 2, got {prev_attempt!r}"
                )
            attempt = prev_attempt + 1

        if attempt not in (1, 2, 3) or attempt not in RESCOPE_INTENT_FILENAMES:
            raise WaitIntentError(f"invalid derived attempt: {attempt!r}")

        _read_and_verify_marker(epoch_dir, attempt)

        final_repo = validate_repository(norm_root, require_clean=True)
        final_norm_repo = validate_accepted_repository(final_repo)
        if final_norm_repo != norm_repo:
            raise WaitIntentError("repository state drifted during pending intent verification")

        return epoch, attempt, deepcopy(norm_repo)
    except WaitIntentError:
        raise
    except (BrokerError, AutopilotError, OSError) as exc:
        raise WaitIntentError(f"pending rescope intent verification failed: {exc}") from exc
