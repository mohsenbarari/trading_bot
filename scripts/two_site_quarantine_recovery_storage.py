"""Private one-shot quarantined recovery envelope publisher and reader under AE03."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any

try:
    from scripts.two_site_cold_start_broker_quiescence import (
        hold_idle_broker_client_lock_for_cold_adoption,
    )
    from scripts.two_site_cold_start_adoption_storage import (
        _load_and_audit_accepted_checkpoint,
        _open_validated_state_root,
    )
    from scripts.two_site_maintenance_transition_dir import (
        list_maintenance_transition_leaves,
        open_maintenance_transitions_dir,
    )
    from scripts.two_site_maintenance_transition_file import (
        read_maintenance_transition_bytes,
    )
    from scripts.two_site_maintenance_transition_record import (
        parse_maintenance_transition_record,
    )
    from scripts.two_site_maintenance_transition_storage import (
        _check_stop_and_pending,
        _pin_validated_root,
        _recheck_checkpoint,
        _recheck_repository,
    )
    from scripts.two_site_codex_autopilot import (
        _validate_persisted_repository,
        validate_accepted_repository,
        validate_repository,
    )
    from scripts.two_site_proposal_runner import unique_json
    from scripts.two_site_quarantine_followup_proof import prove_recovery_followups
    from scripts.two_site_quarantine_pair_proof import (
        MAX_FAILED_RESULT_BYTES,
        REPAIR_EVIDENCE_KEYS,
        prove_quarantined_pair,
    )
    from scripts.two_site_quarantine_recovery_record import (
        validate_quarantine_recovery_record,
    )
except ImportError:
    from two_site_cold_start_broker_quiescence import (  # type: ignore[no-redef]
        hold_idle_broker_client_lock_for_cold_adoption,
    )
    from two_site_cold_start_adoption_storage import (  # type: ignore[no-redef]
        _load_and_audit_accepted_checkpoint,
        _open_validated_state_root,
    )
    from two_site_maintenance_transition_dir import (  # type: ignore[no-redef]
        list_maintenance_transition_leaves,
        open_maintenance_transitions_dir,
    )
    from two_site_maintenance_transition_file import (  # type: ignore[no-redef]
        read_maintenance_transition_bytes,
    )
    from two_site_maintenance_transition_record import (  # type: ignore[no-redef]
        parse_maintenance_transition_record,
    )
    from two_site_maintenance_transition_storage import (  # type: ignore[no-redef]
        _check_stop_and_pending,
        _pin_validated_root,
        _recheck_checkpoint,
        _recheck_repository,
    )
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        _validate_persisted_repository,
        validate_accepted_repository,
        validate_repository,
    )
    try:
        from two_site_proposal_runner import unique_json  # type: ignore[no-redef]
    except ImportError:
        def unique_json(text: str) -> Any:  # type: ignore[misc]
            def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                d = dict(pairs)
                if len(d) != len(pairs):
                    raise ValueError("Duplicate key")
                return d
            return json.loads(text, object_pairs_hook=_pairs)
    from two_site_quarantine_followup_proof import prove_recovery_followups  # type: ignore[no-redef]
    from two_site_quarantine_pair_proof import (  # type: ignore[no-redef]
        MAX_FAILED_RESULT_BYTES,
        REPAIR_EVIDENCE_KEYS,
        prove_quarantined_pair,
    )
    from two_site_quarantine_recovery_record import (  # type: ignore[no-redef]
        validate_quarantine_recovery_record,
    )

RECOVERY_LEAF_NAME: str = "quarantine-recovery.json"
MAX_ENVELOPE_BYTES: int = 32768
_TEMP_PREFIX: str = ".quarantine-recovery."
ENVELOPE_SCHEMA_VERSION: str = "1.0"
VALIDATION_SCHEMA_VERSION: str = "1.0"
ENVELOPE_KEYS = frozenset({"schema_version", "record", "validation"})
VALIDATION_KEYS = frozenset({
    "schema_version",
    "repository_head",
    "repair_evidence",
    "postcommit_tests",
})
POSTCOMMIT_TEST_KEYS = frozenset({"command", "passed_count", "returncode"})


class QuarantineRecoveryStorageError(ValueError):
    """Raised when quarantine recovery storage operations fail."""


__all__ = [
    "MAX_ENVELOPE_BYTES",
    "QuarantineRecoveryStorageError",
    "RECOVERY_LEAF_NAME",
    "publish_quarantine_recovery_locked",
    "read_quarantine_recovery_locked",
]


def _stat_fingerprint(st: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        st.st_dev,
        st.st_ino,
        stat.S_IFMT(st.st_mode),
        st.st_mode & 0o777,
        st.st_uid,
        st.st_size,
    )


def _canonical_json_bytes(obj: Any) -> bytes:
    try:
        raw_text = (
            json.dumps(
                obj,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        return raw_text.encode("utf-8")
    except Exception as exc:
        raise QuarantineRecoveryStorageError(
            "Object cannot be serialized to canonical JSON"
        ) from exc


def _assert_no_own_orphan_temp(state_root_fd: int) -> None:
    scan_fd: int | None = None
    try:
        try:
            scanner = os.scandir(state_root_fd)
        except TypeError:
            scan_fd = os.dup(state_root_fd)
            scanner = os.scandir(scan_fd)
        with scanner:
            for entry in scanner:
                if entry.name.startswith(_TEMP_PREFIX):
                    raise QuarantineRecoveryStorageError(
                        f"Found own orphan quarantine recovery temporary artifact: {entry.name}"
                    )
    except QuarantineRecoveryStorageError:
        raise
    except Exception as exc:
        raise QuarantineRecoveryStorageError(
            f"Failed to scan state_root for temp files: {exc}"
        ) from exc
    finally:
        if scan_fd is not None:
            try:
                os.close(scan_fd)
            except OSError:
                pass


def _validate_postcommit_tests(pct: Any) -> dict[str, Any]:
    if type(pct) is not dict:
        raise QuarantineRecoveryStorageError("postcommit_tests must be a dict")
    if set(pct.keys()) != POSTCOMMIT_TEST_KEYS or len(pct) != len(POSTCOMMIT_TEST_KEYS):
        raise QuarantineRecoveryStorageError("postcommit_tests keys mismatch")

    cmd = pct.get("command")
    if type(cmd) is not str:
        raise QuarantineRecoveryStorageError("postcommit_tests command must be a string")
    if not (1 <= len(cmd) <= 2048):
        raise QuarantineRecoveryStorageError("postcommit_tests command length out of bounds")
    if any(ord(c) < 32 or ord(c) == 127 for c in cmd):
        raise QuarantineRecoveryStorageError(
            "postcommit_tests command contains control characters or NUL"
        )
    if cmd != cmd.strip() or not cmd.strip():
        raise QuarantineRecoveryStorageError(
            "postcommit_tests command must be normalized without leading or trailing whitespace"
        )

    tokens = cmd.strip().split()
    if not tokens:
        raise QuarantineRecoveryStorageError("postcommit_tests command is empty")
    if cmd.strip() in {"pytest", "py.test", "python -m pytest", "python3 -m pytest"}:
        raise QuarantineRecoveryStorageError(
            "postcommit_tests command cannot be bare pytest"
        )
    if tokens in [["pytest"], ["py.test"], ["python", "-m", "pytest"], ["python3", "-m", "pytest"]]:
        raise QuarantineRecoveryStorageError(
            "postcommit_tests command cannot be bare pytest"
        )
    if tokens[0] in {"pytest", "py.test"} and all(t.startswith("-") for t in tokens[1:]):
        raise QuarantineRecoveryStorageError(
            "postcommit_tests command cannot be bare pytest without target"
        )
    if len(tokens) >= 3 and tokens[:3] in (["python", "-m", "pytest"], ["python3", "-m", "pytest"]) and all(t.startswith("-") for t in tokens[3:]):
        raise QuarantineRecoveryStorageError(
            "postcommit_tests command cannot be bare pytest without target"
        )
    if len(tokens) == 1 and tokens[0] in {"test", "pytest", "py.test", "python", "sh", "bash"}:
        raise QuarantineRecoveryStorageError("postcommit_tests command is ambiguous")

    passed_count = pct.get("passed_count")
    if type(passed_count) is not int or isinstance(passed_count, bool) or passed_count <= 0:
        raise QuarantineRecoveryStorageError(
            "postcommit_tests passed_count must be a positive integer and not bool"
        )

    returncode = pct.get("returncode")
    if type(returncode) is not int or isinstance(returncode, bool) or returncode != 0:
        raise QuarantineRecoveryStorageError(
            "postcommit_tests returncode must be exact int 0"
        )

    return {"command": cmd, "passed_count": passed_count, "returncode": returncode}


def _read_failed_result_bytes(state_root_fd: int, failed_run_id: str) -> bytes:
    runs_fd: int | None = None
    run_id_fd: int | None = None
    file_fd: int | None = None
    try:
        runs_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            runs_fd = os.open("runs", runs_flags, dir_fd=state_root_fd)
        except OSError as exc:
            raise QuarantineRecoveryStorageError("Failed to open runs directory") from exc

        st_runs = os.fstat(runs_fd)
        if (
            stat.S_ISLNK(st_runs.st_mode)
            or not stat.S_ISDIR(st_runs.st_mode)
            or st_runs.st_uid != os.geteuid()
        ):
            raise QuarantineRecoveryStorageError("Invalid runs directory descriptor")

        try:
            run_id_fd = os.open(failed_run_id, runs_flags, dir_fd=runs_fd)
        except OSError as exc:
            raise QuarantineRecoveryStorageError(
                f"Failed to open run directory for {failed_run_id}"
            ) from exc

        st_run = os.fstat(run_id_fd)
        if (
            stat.S_ISLNK(st_run.st_mode)
            or not stat.S_ISDIR(st_run.st_mode)
            or st_run.st_uid != os.geteuid()
        ):
            raise QuarantineRecoveryStorageError(
                f"Invalid run directory descriptor for {failed_run_id}"
            )

        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            file_fd = os.open("result.json", file_flags, dir_fd=run_id_fd)
        except OSError as exc:
            raise QuarantineRecoveryStorageError(
                f"Failed to open result.json for {failed_run_id}"
            ) from exc

        st_file = os.fstat(file_fd)
        if (
            stat.S_ISLNK(st_file.st_mode)
            or not stat.S_ISREG(st_file.st_mode)
            or st_file.st_uid != os.geteuid()
        ):
            raise QuarantineRecoveryStorageError("Invalid result.json descriptor")
        if st_file.st_size <= 0 or st_file.st_size > MAX_FAILED_RESULT_BYTES:
            raise QuarantineRecoveryStorageError("result.json size out of bounds")

        chunks: list[bytes] = []
        total = 0
        read_limit = MAX_FAILED_RESULT_BYTES + 1
        while total < read_limit:
            chunk = os.read(file_fd, min(4096, read_limit - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)

        data = b"".join(chunks)
        if len(data) != st_file.st_size or len(data) > MAX_FAILED_RESULT_BYTES:
            raise QuarantineRecoveryStorageError("result.json read size mismatch")
        return data
    finally:
        for descriptor in (file_fd, run_id_fd, runs_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _validate_envelope_and_state(
    root: Path,
    state_root: Path,
    state_root_fd: int,
    envelope: dict[str, Any],
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    if type(envelope) is not dict:
        raise QuarantineRecoveryStorageError("envelope must be a dict")
    if set(envelope.keys()) != ENVELOPE_KEYS or len(envelope) != len(ENVELOPE_KEYS):
        raise QuarantineRecoveryStorageError("envelope keys mismatch")
    if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
        raise QuarantineRecoveryStorageError("envelope schema_version must be 1.0")

    try:
        record = validate_quarantine_recovery_record(envelope.get("record"))
    except Exception as exc:
        raise QuarantineRecoveryStorageError(f"record validation failed: {exc}") from exc

    validation = envelope.get("validation")
    if type(validation) is not dict:
        raise QuarantineRecoveryStorageError("validation must be a dict")
    if set(validation.keys()) != VALIDATION_KEYS or len(validation) != len(VALIDATION_KEYS):
        raise QuarantineRecoveryStorageError("validation keys mismatch")
    if validation.get("schema_version") != VALIDATION_SCHEMA_VERSION:
        raise QuarantineRecoveryStorageError("validation schema_version must be 1.0")

    if validation.get("repository_head") != record["new_repository"]["head"]:
        raise QuarantineRecoveryStorageError(
            "validation repository_head does not match record new_repository head"
        )

    repair_evidence = validation.get("repair_evidence")
    if type(repair_evidence) is not dict or set(repair_evidence.keys()) != REPAIR_EVIDENCE_KEYS:
        raise QuarantineRecoveryStorageError("validation repair_evidence keys mismatch")

    norm_pct = _validate_postcommit_tests(validation.get("postcommit_tests"))
    norm_validation = {
        "postcommit_tests": norm_pct,
        "repair_evidence": copy.deepcopy(repair_evidence),
        "repository_head": validation["repository_head"],
        "schema_version": VALIDATION_SCHEMA_VERSION,
    }

    val_canonical_bytes = _canonical_json_bytes(norm_validation)
    val_sha = hashlib.sha256(val_canonical_bytes).hexdigest()
    if val_sha != record["final_validation_sha256"]:
        raise QuarantineRecoveryStorageError(
            f"validation canonical SHA256 {val_sha} does not match record final_validation_sha256"
        )

    canonical_env = {
        "record": record,
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "validation": norm_validation,
    }
    canonical_env_bytes = _canonical_json_bytes(canonical_env)
    if len(canonical_env_bytes) > MAX_ENVELOPE_BYTES:
        raise QuarantineRecoveryStorageError("Envelope exceeds maximum allowed size")

    try:
        chk_obj, chk_sha = _load_and_audit_accepted_checkpoint(state_root_fd, state_root)
    except Exception as exc:
        raise QuarantineRecoveryStorageError(
            f"Checkpoint load and audit failed: {exc}"
        ) from exc

    if chk_sha != record["prior_checkpoint_sha256"]:
        raise QuarantineRecoveryStorageError(
            "Checkpoint SHA256 does not match record prior_checkpoint_sha256"
        )

    chk_raw_repo = chk_obj.get("repository") if isinstance(chk_obj, dict) else None
    if not isinstance(chk_raw_repo, dict):
        raise QuarantineRecoveryStorageError("Checkpoint missing repository dict")
    try:
        chk_norm_repo = _validate_persisted_repository(chk_raw_repo)
    except Exception as exc:
        raise QuarantineRecoveryStorageError(
            f"Checkpoint repository normalization failed: {exc}"
        ) from exc
    chk_head = chk_norm_repo["head"]

    transitions_fd: int | None = None
    prior_trans_bytes: bytes | None = None
    prior_trans_record: dict[str, Any] | None = None
    try:
        transitions_fd = open_maintenance_transitions_dir(state_root_fd, create=False)
        if transitions_fd is None:
            raise QuarantineRecoveryStorageError(
                "maintenance-transitions directory does not exist"
            )
        leaves = list_maintenance_transition_leaves(transitions_fd)
        matching_leaves: list[tuple[str, bytes, dict[str, Any]]] = []
        for leaf in leaves:
            raw_leaf = read_maintenance_transition_bytes(transitions_fd, leaf)
            if raw_leaf is None:
                continue
            if hashlib.sha256(raw_leaf).hexdigest() == record["prior_maintenance_transition_sha256"]:
                try:
                    parsed = parse_maintenance_transition_record(raw_leaf)
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(
                        f"Prior maintenance transition parsing failed: {exc}"
                    ) from exc
                matching_leaves.append((leaf, raw_leaf, parsed))

        if len(matching_leaves) == 0:
            raise QuarantineRecoveryStorageError(
                "No prior maintenance transition leaf matches prior_maintenance_transition_sha256"
            )
        if len(matching_leaves) > 1:
            raise QuarantineRecoveryStorageError(
                "Multiple prior maintenance transition leaves match prior_maintenance_transition_sha256"
            )
        _, prior_trans_bytes, prior_trans_record = matching_leaves[0]
    finally:
        if transitions_fd is not None:
            try:
                os.close(transitions_fd)
            except OSError:
                pass

    if chk_head != prior_trans_record["prior_head"]:
        raise QuarantineRecoveryStorageError(
            f"Checkpoint repository head {chk_head} does not match prior_transition prior_head {prior_trans_record['prior_head']}"
        )

    failed_result_bytes = _read_failed_result_bytes(state_root_fd, record["failed_run_id"])
    if hashlib.sha256(failed_result_bytes).hexdigest() != record["failed_result_sha256"]:
        raise QuarantineRecoveryStorageError(
            "failed_result_bytes SHA256 does not match record failed_result_sha256"
        )

    try:
        live_raw = validate_repository(root, require_clean=True)
        live_norm = validate_accepted_repository(live_raw)
    except Exception as exc:
        raise QuarantineRecoveryStorageError(
            f"Live repository validation failed: {exc}"
        ) from exc

    chk_protected_refs = chk_norm_repo.get("protected_refs")
    live_protected_refs = live_norm.get("protected_refs")
    if (
        type(chk_protected_refs) is not dict
        or type(live_protected_refs) is not dict
        or chk_protected_refs != live_protected_refs
        or any(
            type(k) is not str or type(v) is not str
            for k, v in chk_protected_refs.items()
        )
        or any(
            type(k) is not str or type(v) is not str
            for k, v in live_protected_refs.items()
        )
    ):
        raise QuarantineRecoveryStorageError(
            "Checkpoint repository protected_refs does not match live repository protected_refs"
        )

    if live_norm != record["new_repository"]:
        raise QuarantineRecoveryStorageError(
            "Live accepted repository does not match record new_repository"
        )

    try:
        prove_quarantined_pair(
            root,
            record,
            prior_trans_bytes,
            failed_result_bytes,
            norm_validation["repair_evidence"],
        )
    except Exception as exc:
        raise QuarantineRecoveryStorageError(f"prove_quarantined_pair failed: {exc}") from exc

    try:
        prove_recovery_followups(root, record)
    except Exception as exc:
        raise QuarantineRecoveryStorageError(f"prove_recovery_followups failed: {exc}") from exc

    return canonical_env, canonical_env_bytes, chk_norm_repo


def _read_and_audit_leaf(state_root_fd: int) -> bytes | None:
    try:
        st_path_before = os.stat(RECOVERY_LEAF_NAME, dir_fd=state_root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise QuarantineRecoveryStorageError("Failed to stat recovery leaf") from exc

    if stat.S_ISLNK(st_path_before.st_mode):
        raise QuarantineRecoveryStorageError("Recovery leaf must not be a symlink")
    if not stat.S_ISREG(st_path_before.st_mode):
        raise QuarantineRecoveryStorageError("Recovery leaf must be a regular file")
    if st_path_before.st_uid != os.geteuid():
        raise QuarantineRecoveryStorageError("Recovery leaf owner mismatch")
    if (st_path_before.st_mode & 0o777) != 0o600:
        raise QuarantineRecoveryStorageError("Recovery leaf mode must be 0600")
    if st_path_before.st_nlink != 1:
        raise QuarantineRecoveryStorageError("Recovery leaf must not have hard links")
    if st_path_before.st_size <= 0 or st_path_before.st_size > MAX_ENVELOPE_BYTES:
        raise QuarantineRecoveryStorageError("Recovery leaf size out of bounds")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_fd: int | None = None
    try:
        try:
            file_fd = os.open(RECOVERY_LEAF_NAME, flags, dir_fd=state_root_fd)
        except OSError as exc:
            raise QuarantineRecoveryStorageError("Failed to open recovery leaf") from exc

        st_fd_before = os.fstat(file_fd)
        if _stat_fingerprint(st_fd_before) != _stat_fingerprint(st_path_before):
            raise QuarantineRecoveryStorageError(
                "Recovery leaf metadata mismatch between path and descriptor"
            )

        chunks: list[bytes] = []
        total = 0
        read_limit = MAX_ENVELOPE_BYTES + 1
        while total < read_limit:
            chunk = os.read(file_fd, min(4096, read_limit - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)

        raw = b"".join(chunks)
        if len(raw) != st_fd_before.st_size or len(raw) > MAX_ENVELOPE_BYTES:
            raise QuarantineRecoveryStorageError("Recovery leaf read size mismatch")

        st_fd_after = os.fstat(file_fd)
        if _stat_fingerprint(st_fd_after) != _stat_fingerprint(st_fd_before):
            raise QuarantineRecoveryStorageError(
                "Recovery leaf descriptor metadata changed during read"
            )

        st_path_after = os.stat(RECOVERY_LEAF_NAME, dir_fd=state_root_fd, follow_symlinks=False)
        if _stat_fingerprint(st_path_after) != _stat_fingerprint(st_path_before):
            raise QuarantineRecoveryStorageError(
                "Recovery leaf path metadata changed during read"
            )

        return raw
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass


def publish_quarantine_recovery_locked(
    root: Path,
    state_root: Path,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Publish a quarantined recovery envelope atomically under existing broker client lock."""
    if not isinstance(root, Path):
        raise QuarantineRecoveryStorageError("root must be a pathlib.Path instance")
    if not isinstance(state_root, Path):
        raise QuarantineRecoveryStorageError("state_root must be a pathlib.Path instance")

    expected_state_root = root / ".local" / "two-site-refactor" / "autopilot"
    if state_root != expected_state_root:
        raise QuarantineRecoveryStorageError(
            f"state_root must be exactly {expected_state_root}, got {state_root}"
        )

    try:
        with hold_idle_broker_client_lock_for_cold_adoption(root):
            root_fd: int | None = None
            state_root_fd: int | None = None
            try:
                try:
                    root_fd = _pin_validated_root(root)
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(f"Root pinning failed: {exc}") from exc
                try:
                    state_root_fd = _open_validated_state_root(state_root)
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(f"State root validation failed: {exc}") from exc
                try:
                    _check_stop_and_pending(root_fd, state_root_fd, state_root)
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(f"STOP or pending check failed: {exc}") from exc
                _assert_no_own_orphan_temp(state_root_fd)

                try:
                    os.stat(RECOVERY_LEAF_NAME, dir_fd=state_root_fd, follow_symlinks=False)
                    raise QuarantineRecoveryStorageError("quarantine-recovery.json already exists")
                except FileNotFoundError:
                    pass

                norm_env, canonical_bytes, chk_norm_repo = _validate_envelope_and_state(
                    root, state_root, state_root_fd, envelope
                )

                token = secrets.token_hex(8)
                temp_name = f"{_TEMP_PREFIX}{os.getpid()}.{token}.tmp"
                temp_fd: int | None = None
                try:
                    flags = (
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                    try:
                        temp_fd = os.open(temp_name, flags, mode=0o600, dir_fd=state_root_fd)
                    except OSError as exc:
                        raise QuarantineRecoveryStorageError(
                            f"Failed to create temp file {temp_name}"
                        ) from exc

                    try:
                        os.fchmod(temp_fd, 0o600)
                    except OSError as exc:
                        raise QuarantineRecoveryStorageError("Failed to fchmod temp file") from exc

                    total_written = 0
                    while total_written < len(canonical_bytes):
                        written = os.write(temp_fd, canonical_bytes[total_written:])
                        if written <= 0:
                            raise QuarantineRecoveryStorageError(
                                "Failed to write full canonical bytes to temp file"
                            )
                        total_written += written

                    try:
                        os.fsync(temp_fd)
                    except OSError as exc:
                        raise QuarantineRecoveryStorageError("Failed to fsync temp file") from exc

                    os.close(temp_fd)
                    temp_fd = None

                    try:
                        os.link(
                            temp_name,
                            RECOVERY_LEAF_NAME,
                            src_dir_fd=state_root_fd,
                            dst_dir_fd=state_root_fd,
                            follow_symlinks=False,
                        )
                    except FileExistsError as exc:
                        raise QuarantineRecoveryStorageError(
                            "quarantine-recovery.json already exists"
                        ) from exc
                    except OSError as exc:
                        raise QuarantineRecoveryStorageError(
                            f"Failed to link {temp_name} to {RECOVERY_LEAF_NAME}"
                        ) from exc

                    try:
                        os.unlink(temp_name, dir_fd=state_root_fd)
                    except OSError as exc:
                        raise QuarantineRecoveryStorageError(
                            f"Failed to unlink temp file {temp_name}"
                        ) from exc

                    try:
                        os.fsync(state_root_fd)
                    except OSError as exc:
                        raise QuarantineRecoveryStorageError(
                            "Failed to fsync state_root directory"
                        ) from exc
                finally:
                    if temp_fd is not None:
                        try:
                            os.close(temp_fd)
                        except OSError:
                            pass
                    try:
                        os.unlink(temp_name, dir_fd=state_root_fd)
                    except OSError:
                        pass

                try:
                    _check_stop_and_pending(root_fd, state_root_fd, state_root)
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(
                        f"STOP or pending recheck failed: {exc}"
                    ) from exc

                try:
                    _recheck_repository(root, norm_env["record"]["new_repository"])
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(
                        f"Repository recheck failed: {exc}"
                    ) from exc

                try:
                    _recheck_checkpoint(
                        state_root_fd,
                        state_root,
                        norm_env["record"]["prior_checkpoint_sha256"],
                        chk_norm_repo,
                    )
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(
                        f"Checkpoint recheck failed: {exc}"
                    ) from exc

                read_back_bytes = _read_and_audit_leaf(state_root_fd)
                if read_back_bytes is None:
                    raise QuarantineRecoveryStorageError(
                        "quarantine-recovery.json missing immediately after publication"
                    )
                if read_back_bytes != canonical_bytes:
                    raise QuarantineRecoveryStorageError(
                        "quarantine-recovery.json read back bytes mismatch"
                    )

                return norm_env
            finally:
                for descriptor in (state_root_fd, root_fd):
                    if descriptor is not None:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
    except (KeyboardInterrupt, SystemExit, QuarantineRecoveryStorageError):
        raise
    except Exception as exc:
        raise QuarantineRecoveryStorageError(
            f"Broker quiescence lock failed: {exc}"
        ) from exc


def read_quarantine_recovery_locked(
    root: Path,
    state_root: Path,
) -> dict[str, Any] | None:
    """Read and validate quarantined recovery envelope under existing broker client lock."""
    if not isinstance(root, Path):
        raise QuarantineRecoveryStorageError("root must be a pathlib.Path instance")
    if not isinstance(state_root, Path):
        raise QuarantineRecoveryStorageError("state_root must be a pathlib.Path instance")

    expected_state_root = root / ".local" / "two-site-refactor" / "autopilot"
    if state_root != expected_state_root:
        raise QuarantineRecoveryStorageError(
            f"state_root must be exactly {expected_state_root}, got {state_root}"
        )

    try:
        with hold_idle_broker_client_lock_for_cold_adoption(root):
            root_fd: int | None = None
            state_root_fd: int | None = None
            try:
                try:
                    root_fd = _pin_validated_root(root)
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(f"Root pinning failed: {exc}") from exc
                try:
                    state_root_fd = _open_validated_state_root(state_root)
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(f"State root validation failed: {exc}") from exc
                try:
                    _check_stop_and_pending(root_fd, state_root_fd, state_root)
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(f"STOP or pending check failed: {exc}") from exc

                raw_bytes = _read_and_audit_leaf(state_root_fd)
                if raw_bytes is None:
                    _assert_no_own_orphan_temp(state_root_fd)
                    return None

                _assert_no_own_orphan_temp(state_root_fd)

                try:
                    text = raw_bytes.decode("utf-8")
                    parsed = unique_json(text)
                except UnicodeDecodeError as exc:
                    raise QuarantineRecoveryStorageError("Envelope is not valid UTF-8") from exc
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(
                        f"Envelope JSON parse failed: {exc}"
                    ) from exc

                norm_env, canonical_bytes, chk_norm_repo = _validate_envelope_and_state(
                    root, state_root, state_root_fd, parsed
                )
                if raw_bytes != canonical_bytes:
                    raise QuarantineRecoveryStorageError(
                        "Envelope bytes are not canonical JSON serialization"
                    )

                try:
                    _check_stop_and_pending(root_fd, state_root_fd, state_root)
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(
                        f"STOP or pending recheck failed: {exc}"
                    ) from exc

                try:
                    _recheck_repository(root, norm_env["record"]["new_repository"])
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(
                        f"Repository recheck failed: {exc}"
                    ) from exc

                try:
                    _recheck_checkpoint(
                        state_root_fd,
                        state_root,
                        norm_env["record"]["prior_checkpoint_sha256"],
                        chk_norm_repo,
                    )
                except Exception as exc:
                    raise QuarantineRecoveryStorageError(
                        f"Checkpoint recheck failed: {exc}"
                    ) from exc

                read_back_bytes = _read_and_audit_leaf(state_root_fd)
                if read_back_bytes is None:
                    raise QuarantineRecoveryStorageError(
                        "quarantine-recovery.json missing during reader leaf recheck"
                    )
                if read_back_bytes != canonical_bytes:
                    raise QuarantineRecoveryStorageError(
                        "quarantine-recovery.json read back bytes mismatch"
                    )

                return norm_env
            finally:
                for descriptor in (state_root_fd, root_fd):
                    if descriptor is not None:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
    except (KeyboardInterrupt, SystemExit, QuarantineRecoveryStorageError):
        raise
    except Exception as exc:
        raise QuarantineRecoveryStorageError(
            f"Broker quiescence lock failed: {exc}"
        ) from exc
