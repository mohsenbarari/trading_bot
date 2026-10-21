"""Bridge between Supervisor cycle completion and durable quota wait admission."""

from __future__ import annotations

import os
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import stat
import time
import uuid
from typing import Any

from scripts.two_site_provider_broker import (
    BrokerError,
    _open_existing_cycle_run_directory,
    canonical_root,
)
from scripts.two_site_quota_disposition_probe import (
    QuotaProbeError,
    probe_cycle_quota_receipt,
)
from scripts.two_site_quota_wait_admission import (
    WaitAdmissionError,
    admit_initial_quota_wait_locked,
)

__all__ = [
    "QuotaSupervisorBridgeError",
    "accept_cycle_quota_disposition_locked",
]

_HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class QuotaSupervisorBridgeError(ValueError):
    """Raised when supervisor bridge operations fail or input is invalid."""


def accept_cycle_quota_disposition_locked(
    root: Path,
    run_dir: Path,
    result: object,
    *,
    enabled: bool = False,
) -> dict | None:
    """Evaluate completed cycle quota disposition and admit initial quota wait if gated.

    Precondition: Caller MUST already own the existing exclusive Supervisor lock.
    This bridge does NOT acquire another lock, call Provider, authorize replay,
    schedule wakeups, or wire runtime hooks.
    """
    if type(enabled) is not bool:
        raise QuotaSupervisorBridgeError("enabled must be an exact bool")
    if not enabled:
        return None

    if not isinstance(root, Path):
        raise QuotaSupervisorBridgeError("root must be a Path")
    if not isinstance(run_dir, Path):
        raise QuotaSupervisorBridgeError("run_dir must be a Path")
    if not isinstance(result, dict):
        raise QuotaSupervisorBridgeError("result must be a dict")

    try:
        c_root = canonical_root(root)
        if c_root != root:
            raise QuotaSupervisorBridgeError("root must be a canonical path")

        runs_dir = root / ".local" / "two-site-refactor" / "autopilot" / "runs"
        cid = run_dir.name
        if not cid or cid in {".", ".."}:
            raise QuotaSupervisorBridgeError("run_dir has invalid cycle run ID")
        if run_dir.parent != runs_dir or run_dir != runs_dir / cid:
            raise QuotaSupervisorBridgeError(
                "run_dir must be directly under root/.local/two-site-refactor/autopilot/runs"
            )

        with _open_existing_cycle_run_directory(root, cid) as (
            pinned_path,
            run_fd,
            verify_identity,
        ):
            if pinned_path != run_dir:
                raise QuotaSupervisorBridgeError(
                    "pinned path does not match supplied run_dir"
                )

            st_before = os.lstat(run_dir)
            if stat.S_ISLNK(st_before.st_mode) or not stat.S_ISDIR(st_before.st_mode):
                raise QuotaSupervisorBridgeError(
                    "run_dir must be a directory and not a symlink"
                )
            if st_before.st_uid != os.geteuid():
                raise QuotaSupervisorBridgeError("run_dir owner mismatch")
            if stat.S_IMODE(st_before.st_mode) != 0o700:
                raise QuotaSupervisorBridgeError("run_dir mode must be 0700")

            fd_stat = os.fstat(run_fd)
            if not stat.S_ISDIR(fd_stat.st_mode):
                raise QuotaSupervisorBridgeError("pinned descriptor is not a directory")
            if fd_stat.st_uid != os.geteuid():
                raise QuotaSupervisorBridgeError("pinned descriptor owner mismatch")
            if stat.S_IMODE(fd_stat.st_mode) != 0o700:
                raise QuotaSupervisorBridgeError("pinned descriptor mode must be 0700")
            if (st_before.st_dev, st_before.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
                raise QuotaSupervisorBridgeError(
                    "run_dir stat does not match pinned descriptor inode"
                )

            verify_identity()

            st_after = os.lstat(run_dir)
            if stat.S_ISLNK(st_after.st_mode) or not stat.S_ISDIR(st_after.st_mode):
                raise QuotaSupervisorBridgeError(
                    "run_dir must remain a directory and not a symlink"
                )
            if st_after.st_uid != os.geteuid() or stat.S_IMODE(st_after.st_mode) != 0o700:
                raise QuotaSupervisorBridgeError("run_dir mode or owner changed")
            if (st_after.st_dev, st_after.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
                raise QuotaSupervisorBridgeError(
                    "run_dir inode changed during verification"
                )

        disposition = probe_cycle_quota_receipt(root, cid)
        if disposition in ("NO_RESERVATION", "NO_TERMINAL_QUOTA"):
            return None

        if disposition != "TERMINAL_QUOTA":
            raise QuotaSupervisorBridgeError(
                f"unexpected quota disposition: {disposition}"
            )

        if result.get("state") != "BLOCKED":
            raise QuotaSupervisorBridgeError(
                "result state must be BLOCKED for terminal quota"
            )

        if result.get("head_changed") is not False:
            raise QuotaSupervisorBridgeError(
                "result head_changed must be exactly False for terminal quota"
            )

        if result.get("commit") is not None:
            raise QuotaSupervisorBridgeError(
                "result commit must be None for terminal quota"
            )

        head_before = result.get("head_before")
        head_after = result.get("head_after")
        if (
            not isinstance(head_before, str)
            or not isinstance(head_after, str)
            or not _HEX_SHA_RE.fullmatch(head_before)
            or head_before != head_after
        ):
            raise QuotaSupervisorBridgeError(
                "result head_before and head_after must be identical 40-character lowercase hex SHAs"
            )

        if (
            result.get("gemini_worker_run_id") is not None
            or result.get("gemini_reviewer_run_id") is not None
            or result.get("proposal_sha256") is not None
        ):
            raise QuotaSupervisorBridgeError(
                "worker run ID, reviewer run ID, and proposal SHA must be None for terminal quota"
            )

        wait_record = admit_initial_quota_wait_locked(root, cid)
        if (
            not isinstance(wait_record, dict)
            or wait_record.get("state") != "WAITING_QUOTA"
        ):
            raise QuotaSupervisorBridgeError(
                "admitted quota record is invalid or not in WAITING_QUOTA state"
            )

        return wait_record

    except (KeyboardInterrupt, SystemExit):
        raise
    except QuotaSupervisorBridgeError:
        raise
    except (BrokerError, QuotaProbeError, WaitAdmissionError, OSError) as exc:
        raise QuotaSupervisorBridgeError(str(exc)) from exc


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _same(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _check_resume_stop(root: Path) -> None:
    from scripts.two_site_quota_broker_route import _check_pre_dispatch_stop
    try:
        _check_pre_dispatch_stop(root)
    except BrokerError as exc:
        raise QuotaSupervisorBridgeError(str(exc)) from exc
    try:
        (root / "STOP").lstat()
    except FileNotFoundError:
        return
    raise QuotaSupervisorBridgeError("quota resume stopped")


@contextmanager
def quota_codex_process(command: list[str], root: Path, environment: dict[str, str]):
    """Quota-only plain Popen owner: no unbounded Popen.__exit__ under holds."""
    import signal
    import subprocess
    from scripts.two_site_proposal_runner import terminate_process

    process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    primary = None
    try:
        yield process
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_error = None
        try:
            if process.returncode is None:
                try:
                    terminate_process(process)  # Existing SIGKILL + wait(timeout=10).
                except BaseException as exc:
                    cleanup_error = exc
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=10)
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, name, None)
            if stream is not None:
                try:
                    stream.close()
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
        if primary is None and cleanup_error is not None:
            raise cleanup_error


def _consume_exact_quota_response(root: Path, acquired: dict) -> None:
    """Caller holds accepted client quiescence; retain every durable receipt."""
    from scripts import two_site_provider_broker as broker
    from scripts.two_site_quota_continuation_terminal_storage import read_terminal_continuation_record

    request = acquired["request"]
    record = read_terminal_continuation_record(root, request["request_id"], expected_request=request)
    if (hashlib.sha256(_canonical(record)).hexdigest() != acquired["terminal_receipt_sha256"]
            or not _same(record["response"], acquired["response"])):
        raise QuotaSupervisorBridgeError("durable terminal response drift before cleanup")
    responses = root / broker.STATE_REL / "responses"
    path = broker._response_path(responses, request["request_id"])
    descriptor = os.open(responses, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    failure = None
    try:
        parent = os.fstat(descriptor)
        if (responses.resolve(strict=True) != responses or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) != 0o700
                or (parent.st_dev, parent.st_ino) != (responses.stat().st_dev, responses.stat().st_ino)):
            raise QuotaSupervisorBridgeError("unsafe response directory")
        before = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        if not _same(broker._safe_read_existing_response(root, path), acquired["response"]):
            raise QuotaSupervisorBridgeError("queue/terminal response mismatch before cleanup")
        after = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        fingerprint = lambda st: (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
                                  st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(path.lstat()):
            raise QuotaSupervisorBridgeError("response inode changed before cleanup")
        _check_resume_stop(root)
        os.unlink(path.name, dir_fd=descriptor)
        os.fsync(descriptor)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            os.close(descriptor)
        except OSError:
            if failure is None:
                raise


@contextmanager
def quota_codex_review_locked(root: Path, original: str, continuation: str, timeout: int):
    """One real allocation/claim, strict READY acquisition, then Codex-only hold.

    Caller owns Supervisor. No ordinary reservation/submit/Worker or replay is
    performed here. A post-claim crash is ambiguous and is never auto-requeued.
    """
    from scripts.two_site_quota_continuation_admission import normal_pipeline_context
    with normal_pipeline_context(root):
        from scripts import two_site_provider_broker as broker
        from scripts.two_site_cold_start_broker_quiescence import hold_idle_broker_client_lock_for_cold_adoption
        from scripts.two_site_quota_supervisor_resume_submit import submit_known_reset_resume_once_locked
        from scripts.two_site_quota_supervisor_resume_context import wait_for_known_reset_resume_ready_locked

        if (not broker.CYCLE_RUN_RE.fullmatch(original) or not broker.CYCLE_RUN_RE.fullmatch(continuation)
                or original == continuation or type(timeout) is not int or not 10 <= timeout <= 900):
            raise QuotaSupervisorBridgeError("invalid actual quota allocation")
        _check_resume_stop(root)
        status_path = broker.broker_paths(root)[1]
        status = broker._read_broker_status(root, status_path)
        identity = (status.get("pid"), status.get("started_at"))
        if (status.get("state") != "IDLE" or status.get("quota_continuation_ready") is not True
                or type(identity[0]) is not int or type(identity[1]) not in (int, float)):
            raise QuotaSupervisorBridgeError("quota Broker is not explicitly ready")
        submitted = submit_known_reset_resume_once_locked(
            root, original, continuation, uuid.uuid4().hex,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), timeout, time.time(),
        )
        request = submitted["request"]
        if request["controller_run_id"] != continuation or request["original_controller_run_id"] != original:
            raise QuotaSupervisorBridgeError("claim/request allocation mismatch")
        acquired = wait_for_known_reset_resume_ready_locked(root, request, expected_broker_identity=identity)
        if not _same(acquired["request"], request):
            raise QuotaSupervisorBridgeError("strict READY request changed before quiescence")
        with hold_idle_broker_client_lock_for_cold_adoption(root):
            current = broker._read_broker_status(root, status_path)
            if (current.get("pid"), current.get("started_at")) != identity:
                raise QuotaSupervisorBridgeError("Broker identity changed before final Codex")
            _consume_exact_quota_response(root, acquired)
            _check_resume_stop(root)
            ready = acquired["ready"]
            context = {key: request[key] for key in (
                "controller_run_id", "original_controller_run_id", "request_id", "task_path",
                "task_sha256", "task_id", "semantic_task_fingerprint", "source_head",
                "assignment_id", "assignment_sha256", "run_prefix",
            )}
            context["ready"] = ready
            context["terminal_receipt_sha256"] = acquired["terminal_receipt_sha256"]
            if len(_canonical(context)) > 16000:
                raise QuotaSupervisorBridgeError("bounded Codex quota handoff exceeded")
            yield context
            _check_resume_stop(root)


def resume_due_quota_cycle_locked(root, codex, state_root, repository, timeout, preparation_route):
    """Existing standing Supervisor branch, never ordinary RESCOPE callbacks."""
    from scripts import two_site_codex_autopilot as ap
    from scripts import two_site_provider_broker as broker
    import math
    from scripts.two_site_quota_supervisor_due_inspect import inspect_durable_quota_wait_due_locked
    from scripts.two_site_quota_wait_recovery import recover_durable_initial_wait_locked

    try:
        _check_resume_stop(root)
        recovered = recover_durable_initial_wait_locked(root)
        if recovered is None:
            raise ap.AutopilotError("no durable quota wait record recovered")
        if type(recovered) is not tuple or len(recovered) != 2:
            raise ap.AutopilotError("recovered quota wait result must be an exact tuple of length 2")
        original, record = recovered
        if type(original) is not str or not ap.RUN_ID.fullmatch(original):
            raise ap.AutopilotError("recovered controller_run_id is invalid")
        if type(record) is not dict:
            raise ap.AutopilotError("recovered quota wait record must be an exact dict")
        if record.get("state") != "WAITING_QUOTA":
            raise ap.AutopilotError("recovered quota wait record state must be WAITING_QUOTA")
        if record.get("controller_run_id") != original:
            raise ap.AutopilotError("recovered quota wait record controller_run_id mismatch")
        if type(record.get("request_id")) is not str or not record["request_id"]:
            raise ap.AutopilotError("recovered quota wait record request_id must be a nonempty str")
        if type(record.get("schedule")) is not dict:
            raise ap.AutopilotError("recovered quota wait record schedule must be an exact dict")
        current = ap.validate_repository(root, require_clean=True)
        if type(current) is not dict or current != repository:
            raise ap.AutopilotError("repository facts drifted or invalid during quota wait recovery")
        if os.path.lexists(state_root / "STOP") or os.path.lexists(root / "STOP"):
            if os.path.islink(state_root / "STOP") and not (state_root / "STOP").exists():
                raise ap.AutopilotError("STOP path is a broken symlink")
            ap.write_supervisor_state(state_root, "STOPPED", cycle=1)
            return 0
        due = inspect_durable_quota_wait_due_locked(root, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        if type(due) is not dict or due.get("controller_run_id") != original:
            raise QuotaSupervisorBridgeError("quota due identity mismatch")
        if due["due"]["state"] == "UNKNOWN_BACKOFF_REVIEW_DUE":
            # A Monitor READY observation starts this existing Supervisor only.
            # It never authorizes a resume claim, Coordinator, or Provider.
            from scripts.two_site_quota_wait_intent import read_pending_rescope_intent
            from scripts.two_site_quota_wait_sources import collect_initial_wait_source_expected
            from scripts.two_site_quota_wait_storage import publish_unknown_health_check, UnknownHealthPublicationPending
            from scripts.two_site_quota_resume_claim_storage import QUOTA_RESUME_CLAIM_FILENAME
            with _open_existing_cycle_run_directory(root, original) as (_, fd, verify):
                verify()
                try:
                    os.stat(QUOTA_RESUME_CLAIM_FILENAME, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise QuotaSupervisorBridgeError("existing claim blocks UNKNOWN health check")
            epoch, attempt, frame = read_pending_rescope_intent(root)
            if frame != ap.validate_accepted_repository(repository) or ap.validate_repository(root, require_clean=True) != repository:
                raise QuotaSupervisorBridgeError("repository drift before UNKNOWN health check")
            expected = collect_initial_wait_source_expected(root, original, epoch, attempt)
            _check_resume_stop(root)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            summary = "UNKNOWN local health observation recorded; no Provider permission."
            try:
                publish_unknown_health_check(root, original, expected, now)
            except UnknownHealthPublicationPending:
                # A validated codec alias is local durability work only. Keep the
                # existing WAITING projection so read-only Monitor can start the
                # same Supervisor owner; never grant claim/Provider authority.
                if ap.validate_repository(root, require_clean=True) != repository:
                    raise QuotaSupervisorBridgeError("repository drift during UNKNOWN durability work")
                summary = "UNKNOWN SAME ordinal pending local durability; no Provider permission."
            _check_resume_stop(root)
            projection = inspect_durable_quota_wait_due_locked(root, now)
            ap.write_supervisor_state(state_root, "WAITING_QUOTA", cycle=1,
                evidence=str(state_root / "runs" / original), head=repository["head"],
                request_id=record["request_id"], schedule=record["schedule"],
                summary=summary,
                quota_health=projection["due"], blockers=[])
            return 2
        if due["due"]["state"] != "KNOWN_RESET_REVALIDATION_DUE":
            ap.write_supervisor_state(state_root, "WAITING_QUOTA", cycle=1,
                evidence=str(state_root / "runs" / original), head=repository["head"],
                request_id=record["request_id"], schedule=record["schedule"],
                summary="Validated terminal quota; awaiting explicit resume gate.", blockers=[])
            return 2
        # A prior claim may have dispatched Provider or Codex. Presence is only
        # a blocking observation, never a mutable-status replay authorization.
        from scripts.two_site_quota_resume_claim_storage import QUOTA_RESUME_CLAIM_FILENAME
        with _open_existing_cycle_run_directory(root, original) as (_path, run_fd, verify):
            verify()
            try:
                os.stat(QUOTA_RESUME_CLAIM_FILENAME, dir_fd=run_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise QuotaSupervisorBridgeError("existing quota claim is ambiguous; never allocate or resubmit")
        broker_status = root / broker.STATE_REL / "status.json"
        ready = False
        if os.path.lexists(broker_status):
            status = broker._read_broker_status(root, broker_status)
            updated, started = status.get("updated_at"), status.get("started_at")
            age = time.time() - updated if type(updated) in (int, float) else float("inf")
            ready = (status.get("state") == "IDLE" and status.get("quota_continuation_ready") is True
                and type(status.get("pid")) is int and status["pid"] > 0
                and type(started) in (int, float) and math.isfinite(started)
                and math.isfinite(age) and 0 <= age <= broker.STATUS_MAX_AGE_SECONDS)
        if not ready:
            ap.write_supervisor_state(state_root, "WAITING_QUOTA", cycle=1,
                evidence=str(state_root / "runs" / original), head=repository["head"],
                request_id=record["request_id"], schedule=record["schedule"],
                summary="Validated terminal quota; awaiting explicit resume gate.", blockers=[])
            return 2
        run_dir, result = ap.run_cycle(root, codex, state_root, 1, timeout,
            preparation_route=preparation_route, quota_original_controller_run_id=original)
        if result["state"] not in {"CONTINUE", "COMPLETE"}:
            raise QuotaSupervisorBridgeError("quota final Codex did not complete acceptance")
        ap.write_supervisor_state(state_root, result["state"], cycle=1,
            evidence=str(run_dir), head=result["head_after"], summary=result["summary"],
            next_action=result["next_action"], blockers=[],
            gemini_worker_run_id=result["gemini_worker_run_id"],
            gemini_reviewer_run_id=result["gemini_reviewer_run_id"], proposal_sha256=result["proposal_sha256"])
        return 0 if result["state"] == "COMPLETE" else 2
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        try:
            ap.write_supervisor_state(state_root, "BLOCKED", cycle=1,
                head=repository["head"], ambiguous_change=True,
                summary="Quota resume hard stop; HEAD is validated entry context, not a current observation.",
                blockers=["quota resume hard stop: " + type(exc).__name__ + ": " + str(exc)[:512]])
        except Exception as status_error:
            raise exc from status_error
        return 2
