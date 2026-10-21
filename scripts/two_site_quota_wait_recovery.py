"""Hermetic recovery of durable initial quota wait records under Supervisor lock."""
from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
import stat
import sys

from scripts.two_site_codex_autopilot import AutopilotError
from scripts.two_site_provider_broker import (
    BrokerError,
    CYCLE_RUN_RE,
    _open_existing_cycle_run_directory,
    canonical_root,
)
from scripts.two_site_quota_wait_intent import (
    WaitIntentError,
    read_pending_rescope_intent,
)
from scripts.two_site_quota_wait_sources import (
    WaitSourceError,
    collect_initial_wait_source_expected,
)
from scripts.two_site_quota_wait_storage import (
    WaitStorageError,
    read_initial_wait_record,
)

__all__ = ["WaitRecoveryError", "recover_durable_initial_wait_locked"]


class WaitRecoveryError(ValueError):
    """Raised when durable initial wait recovery fails."""


def _check_stop_absent(root: Path) -> None:
    stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    try:
        stop_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WaitRecoveryError(
            f"failed checking autopilot STOP sentinel: {exc}"
        ) from exc
    raise WaitRecoveryError("autopilot STOP sentinel exists")


def _recover_durable_initial_wait_unframed(
    root: Path,
    history_guards: ExitStack,
) -> tuple[str, dict] | None:
    """Recover durable initial quota wait record under caller-held Supervisor lock.

    Returns (controller_run_id, record) if an active wait exists, or None if no wait exists.
    """
    if not isinstance(root, Path):
        raise WaitRecoveryError("root must be a Path")

    try:
        norm_root = canonical_root(root)
    except (BrokerError, OSError) as exc:
        raise WaitRecoveryError(f"root canonicalization failed: {exc}") from exc

    if norm_root != root:
        raise WaitRecoveryError(
            f"root must be exact canonical path: expected {norm_root!r}, got {root!r}"
        )

    _check_stop_absent(root)

    pending_error = None
    try:
        pending = _pending_or_lawfully_absent(root)
    except (WaitIntentError, OSError, ValueError) as exc:
        raise WaitRecoveryError("present pending rescope evidence is invalid") from exc
    if pending is None:
        pending_error = WaitIntentError("current pending rescope epoch is lawfully absent")
    else:
        epoch_before, attempt_before, repo_before = pending

    runs_dir = root / ".local" / "two-site-refactor" / "autopilot" / "runs"
    try:
        runs_lstat = runs_dir.lstat()
    except OSError as exc:
        raise WaitRecoveryError(
            f"runs directory missing or inaccessible: {exc}"
        ) from exc

    if stat.S_ISLNK(runs_lstat.st_mode):
        raise WaitRecoveryError(f"runs directory cannot be a symlink: {runs_dir}")
    if not stat.S_ISDIR(runs_lstat.st_mode):
        raise WaitRecoveryError(f"runs directory is not a directory: {runs_dir}")

    expected_uid = os.geteuid()
    if runs_lstat.st_uid != expected_uid:
        raise WaitRecoveryError(
            f"runs directory owned by uid {runs_lstat.st_uid}, expected {expected_uid}"
        )
    if stat.S_IMODE(runs_lstat.st_mode) != 0o700:
        raise WaitRecoveryError(
            f"runs directory mode must be 0700, got {oct(stat.S_IMODE(runs_lstat.st_mode))}"
        )

    runs_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )

    runs_fd: int | None = None
    candidates: list[str] = []
    other_runs: list[str] = []
    validated_continuations: set[str] = set()
    resolved_histories = 0
    history_frame_active = False

    try:
        try:
            runs_fd = os.open(runs_dir, runs_flags)
        except OSError as exc:
            raise WaitRecoveryError(
                f"cannot safely open runs directory: {exc}"
            ) from exc

        try:
            runs_fstat = os.fstat(runs_fd)
        except OSError as exc:
            raise WaitRecoveryError(
                f"cannot fstat runs directory: {exc}"
            ) from exc

        if (
            runs_fstat.st_dev != runs_lstat.st_dev
            or runs_fstat.st_ino != runs_lstat.st_ino
        ):
            raise WaitRecoveryError(
                "runs directory inode changed between lstat and open"
            )
        if runs_fstat.st_uid != expected_uid:
            raise WaitRecoveryError(
                f"runs directory fd owned by uid {runs_fstat.st_uid}, expected {expected_uid}"
            )
        if stat.S_IMODE(runs_fstat.st_mode) != 0o700:
            raise WaitRecoveryError(
                f"runs directory fd mode must be 0700, got {oct(stat.S_IMODE(runs_fstat.st_mode))}"
            )

        entry_count = 0
        try:
            with os.scandir(runs_fd) as scanner:
                for entry in scanner:
                    entry_count += 1
                    if entry_count > 512:
                        raise WaitRecoveryError(
                            "runs directory entry count exceeds maximum limit of 512"
                        )

                    entry_name = entry.name
                    if not CYCLE_RUN_RE.fullmatch(entry_name):
                        raise WaitRecoveryError(
                            f"unexpected entry in runs directory: {entry_name!r}"
                        )

                    try:
                        entry_st = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise WaitRecoveryError(
                            f"cannot stat runs entry {entry_name!r}: {exc}"
                        ) from exc

                    if stat.S_ISLNK(entry_st.st_mode):
                        raise WaitRecoveryError(
                            f"runs entry cannot be a symlink: {entry_name!r}"
                        )
                    if not stat.S_ISDIR(entry_st.st_mode):
                        raise WaitRecoveryError(
                            f"runs entry is not a directory: {entry_name!r}"
                        )

                    sub_flags = (
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0)
                    )
                    sub_fd: int | None = None
                    try:
                        try:
                            sub_fd = os.open(
                                entry_name, sub_flags, dir_fd=runs_fd
                            )
                        except OSError as exc:
                            raise WaitRecoveryError(
                                f"cannot safely open run directory {entry_name!r}: {exc}"
                            ) from exc

                        try:
                            sub_st = os.fstat(sub_fd)
                        except OSError as exc:
                            raise WaitRecoveryError(
                                f"cannot fstat run directory {entry_name!r}: {exc}"
                            ) from exc

                        if (
                            sub_st.st_dev != entry_st.st_dev
                            or sub_st.st_ino != entry_st.st_ino
                        ):
                            raise WaitRecoveryError(
                                f"run directory {entry_name!r} identity changed between stat and open"
                            )

                        try:
                            os.stat(
                                "quota-wait.json",
                                dir_fd=sub_fd,
                                follow_symlinks=False,
                            )
                            wait_exists = True
                        except FileNotFoundError:
                            wait_exists = False
                        except OSError as exc:
                            raise WaitRecoveryError(
                                f"cannot inspect quota-wait.json in {entry_name!r}: {exc}"
                            ) from exc
                        partial_resume = False
                        if not wait_exists:
                            for leaf in ("quota-resume-claim.json", "quota-resume-lineage-snapshot.json",
                                         "quota-resume-submission.json", "accepted-lineage.json", "final-ack.json",
                                         "quota-history-retirement.json", "quota-history-retirement-complete.json"):
                                try:
                                    os.stat(leaf, dir_fd=sub_fd, follow_symlinks=False)
                                except FileNotFoundError:
                                    continue
                                partial_resume = True
                    finally:
                        if sub_fd is not None:
                            try:
                                os.close(sub_fd)
                            except OSError as exc:
                                if sys.exc_info()[1] is None:
                                    raise WaitRecoveryError(
                                        f"cannot safely close descriptor for {entry_name!r}: {exc}"
                                    ) from exc

                    if wait_exists:
                        from scripts.two_site_quota_retention import read_final_ack_history_locked, _current_repository_guard
                        if not history_frame_active and any(os.path.lexists(runs_dir / entry_name / leaf)
                                for leaf in ("quota-resume-claim.json", "quota-resume-lineage-snapshot.json",
                                             "quota-resume-submission.json", "quota-history-retirement.json",
                                             "quota-history-retirement-complete.json")):
                            history_guards.enter_context(_current_repository_guard(root))
                            history_frame_active = True
                        try:
                            history = read_final_ack_history_locked(root, entry_name)
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception as exc:
                            raise WaitRecoveryError("invalid quota history: " + entry_name) from exc
                        if history is not None:
                            resolved_histories += 1
                            validated_continuations.add(history["ack"]["continuation_controller_run_id"])
                        else:
                            candidates.append(entry_name)
                            if len(candidates) > 1:
                                raise WaitRecoveryError(
                                    f"multiple wait candidates found in runs directory: {candidates}"
                                )
                    elif partial_resume:
                        other_runs.append(entry_name)
        except OSError as exc:
            raise WaitRecoveryError(
                f"error scanning runs directory: {exc}"
            ) from exc

        try:
            post_fstat = os.fstat(runs_fd)
            post_lstat = runs_dir.lstat()
        except OSError as exc:
            raise WaitRecoveryError(
                f"cannot re-stat runs directory after scan: {exc}"
            ) from exc

        for attr in (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_mode",
            "st_mtime_ns",
            "st_ctime_ns",
        ):
            if getattr(post_fstat, attr) != getattr(runs_fstat, attr):
                raise WaitRecoveryError(
                    f"runs directory fd metadata changed during scan: {attr}"
                )
            if getattr(post_lstat, attr) != getattr(runs_lstat, attr):
                raise WaitRecoveryError(
                    f"runs directory path metadata changed during scan: {attr}"
                )
    finally:
        if runs_fd is not None:
            try:
                os.close(runs_fd)
            except OSError as exc:
                if sys.exc_info()[1] is None:
                    raise WaitRecoveryError(
                        f"cannot safely close runs directory descriptor: {exc}"
                    ) from exc

    if any(other not in validated_continuations for other in other_runs):
        raise WaitRecoveryError("orphan or partial quota history")
    if not candidates and resolved_histories:
        _check_stop_absent(root)
        return None  # Observational only: never admission or permission to replay.
    if pending_error is not None:
        raise WaitRecoveryError("pending rescope intent verification failed") from pending_error

    if not candidates:
        _check_stop_absent(root)
        try:
            epoch_after, attempt_after, repo_after = read_pending_rescope_intent(root)
        except WaitIntentError as exc:
            raise WaitRecoveryError(
                f"pending rescope intent verification failed: {exc}"
            ) from exc

        if epoch_after != epoch_before:
            raise WaitRecoveryError(
                f"rescope epoch drifted: expected {epoch_before!r}, got {epoch_after!r}"
            )
        if attempt_after != attempt_before:
            raise WaitRecoveryError(
                f"rescope attempt drifted: expected {attempt_before!r}, got {attempt_after!r}"
            )
        if repo_after != repo_before:
            raise WaitRecoveryError("repository facts drifted during recovery")

        return None

    cid = candidates[0]
    try:
        with _open_existing_cycle_run_directory(root, cid) as (
            run_path,
            run_fd,
            verify_identity,
        ):
            verify_identity()
            try:
                wait_st = os.stat(
                    "quota-wait.json", dir_fd=run_fd, follow_symlinks=False
                )
            except OSError as exc:
                raise WaitRecoveryError(
                    f"cannot stat wait file in run directory {cid!r}: {exc}"
                ) from exc

            if stat.S_ISLNK(wait_st.st_mode):
                raise WaitRecoveryError(
                    f"wait file in run directory {cid!r} cannot be a symlink"
                )
            if not stat.S_ISREG(wait_st.st_mode):
                raise WaitRecoveryError(
                    f"wait file in run directory {cid!r} must be a regular file"
                )
            if stat.S_IMODE(wait_st.st_mode) != 0o600:
                raise WaitRecoveryError(
                    f"wait file in run directory {cid!r} mode must be 0600, got {oct(stat.S_IMODE(wait_st.st_mode))}"
                )
            if wait_st.st_uid != expected_uid:
                raise WaitRecoveryError(
                    f"wait file in run directory {cid!r} owned by uid {wait_st.st_uid}, expected {expected_uid}"
                )
            if wait_st.st_nlink != 1:
                raise WaitRecoveryError(
                    f"wait file in run directory {cid!r} hard link count must be 1, got {wait_st.st_nlink}"
                )
            verify_identity()
    except WaitRecoveryError:
        raise
    except (BrokerError, OSError) as exc:
        raise WaitRecoveryError(
            f"failed validating cycle run directory {cid!r}: {exc}"
        ) from exc

    try:
        expected = collect_initial_wait_source_expected(
            root=root,
            controller_run_id=cid,
            rescope_epoch=epoch_before,
            rescope_attempt=attempt_before,
        )
    except WaitSourceError as exc:
        raise WaitRecoveryError(
            f"failed collecting wait source expected for {cid!r}: {exc}"
        ) from exc
    except (BrokerError, AutopilotError) as exc:
        raise WaitRecoveryError(
            f"broker or autopilot error collecting wait source expected for {cid!r}: {exc}"
        ) from exc

    if not isinstance(expected, dict):
        raise WaitRecoveryError(
            f"expected record must be a dict, got {type(expected).__name__}"
        )
    if expected.get("repository") != repo_before:
        raise WaitRecoveryError(
            "expected repository does not match pending rescope intent repository"
        )

    try:
        record = read_initial_wait_record(
            root=root,
            controller_run_id=cid,
            expected=expected,
        )
    except WaitStorageError as exc:
        raise WaitRecoveryError(
            f"failed reading initial wait record for {cid!r}: {exc}"
        ) from exc

    if not isinstance(record, dict):
        raise WaitRecoveryError(
            f"wait record must be a dict, got {type(record).__name__}"
        )
    if record.get("state") != "WAITING_QUOTA":
        raise WaitRecoveryError(
            f"wait record state must be WAITING_QUOTA, got {record.get('state')!r}"
        )
    if record.get("controller_run_id") != cid:
        raise WaitRecoveryError(
            f"wait record controller_run_id mismatch: expected {cid!r}, got {record.get('controller_run_id')!r}"
        )

    _check_stop_absent(root)

    try:
        epoch_after, attempt_after, repo_after = read_pending_rescope_intent(root)
    except WaitIntentError as exc:
        raise WaitRecoveryError(
            f"pending rescope intent verification failed: {exc}"
        ) from exc

    if epoch_after != epoch_before:
        raise WaitRecoveryError(
            f"rescope epoch drifted: expected {epoch_before!r}, got {epoch_after!r}"
        )
    if attempt_after != attempt_before:
        raise WaitRecoveryError(
            f"rescope attempt drifted: expected {attempt_before!r}, got {attempt_after!r}"
        )
    if repo_after != repo_before:
        raise WaitRecoveryError("repository facts drifted during recovery")

    return cid, record


def _pending_or_lawfully_absent(root: Path):
    """Keep the accepted pending-reader ABI; suppress only proved fresh absence."""
    try:
        return read_pending_rescope_intent(root)
    except WaitIntentError as pending:
        from scripts import two_site_codex_autopilot as ap
        from scripts.two_site_quota_retention import _Pins, _bounded_names, _pin_current_repository
        with _Pins() as pins:
            facts = _pin_current_repository(root, pins)
            state = root / ".local/two-site-refactor/autopilot"
            pins.directory(state, private=True)
            epoch = state / ap.rescope_epoch_basename(facts)
            if os.path.lexists(epoch):
                pins.directory(epoch, private=True)
                if _bounded_names(epoch, 6, "current rescope epoch"):
                    raise pending
            pins.verify()
            if os.path.lexists(epoch) and _bounded_names(epoch, 6, "current rescope epoch"):
                raise WaitIntentError("current rescope epoch appeared during absence proof") from pending
            return None


def recover_durable_initial_wait_locked(root: Path) -> tuple[str, dict] | None:
    if not isinstance(root, Path):
        raise WaitRecoveryError("root must be a Path")
    try:
        with ExitStack() as history_guards:
            return _recover_durable_initial_wait_unframed(root, history_guards)
    except WaitRecoveryError:
        raise
    except (OSError, ValueError) as exc:
        raise WaitRecoveryError("current recovery custody failed") from exc
