"""Dormant pure reviewed maintenance-transition storage under AE03.

Provides atomic, read-only loading and validation of reviewed maintenance-transition
records within an exclusive Broker client lock under Supervisor hold.
Validates persisted checkpoint repository via canonical mapping audit and invokes
bounded cold-adoption pending scan directly by state_root Path.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import stat
from typing import Any

from scripts.two_site_cold_start_broker_quiescence import (
    hold_idle_broker_client_lock_for_cold_adoption,
)
from scripts.two_site_cold_start_adoption_storage import (
    _load_and_audit_accepted_checkpoint,
    _open_validated_state_root,
)
from scripts.two_site_cold_start_pending_scan import (
    assert_no_pending_cold_adoption_artifacts_locked,
)
from scripts.two_site_maintenance_transition_dir import (
    list_maintenance_transition_leaves,
    open_maintenance_transitions_dir,
)
from scripts.two_site_maintenance_transition_file import (
    read_maintenance_transition_bytes,
)
from scripts.two_site_maintenance_transition_git import (
    verify_declared_maintenance_git_chain,
)
from scripts.two_site_maintenance_transition_record import (
    parse_maintenance_transition_record,
)
from scripts.two_site_codex_autopilot import (
    _validate_persisted_repository,
    rescope_epoch_basename,
    validate_accepted_repository,
    validate_repository,
)

_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_AUTOPILOT_CONTROL_PATH = "scripts/two_site_codex_autopilot.py"
_STATE_ROOT_REL = Path(".local/two-site-refactor/autopilot")


class MaintenanceTransitionStorageError(Exception):
    """Raised when maintenance transition storage loading or verification fails."""


__all__ = [
    "MaintenanceTransitionStorageError",
    "read_maintenance_transition_locked",
]


def _type_sensitive_equal(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_type_sensitive_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_type_sensitive_equal(x, y) for x, y in zip(a, b))
    return a == b


def _pin_validated_root(root: Path) -> int:
    if not isinstance(root, Path):
        raise MaintenanceTransitionStorageError("root must be a pathlib.Path instance")
    if not root.is_absolute():
        raise MaintenanceTransitionStorageError("root must be an absolute path")
    root_str = str(root)
    if root_str != os.path.normpath(root_str):
        raise MaintenanceTransitionStorageError("root must be canonical without redundant segments")

    curr = root
    while True:
        try:
            st_curr = os.lstat(curr)
        except (KeyboardInterrupt, SystemExit):
            raise
        except FileNotFoundError:
            raise MaintenanceTransitionStorageError("root ancestor does not exist") from None
        except OSError:
            raise MaintenanceTransitionStorageError("Failed to stat root ancestor") from None
        if stat.S_ISLNK(st_curr.st_mode):
            raise MaintenanceTransitionStorageError("root ancestor must not be a symlink")
        parent = curr.parent
        if parent == curr:
            break
        curr = parent

    try:
        real_root = root.resolve(strict=True)
    except (KeyboardInterrupt, SystemExit):
        raise
    except OSError:
        raise MaintenanceTransitionStorageError("Failed to resolve root") from None
    if real_root != root:
        raise MaintenanceTransitionStorageError("root contains symlinks")

    try:
        st_before = os.lstat(root)
    except (KeyboardInterrupt, SystemExit):
        raise
    except FileNotFoundError:
        raise MaintenanceTransitionStorageError("root directory does not exist") from None
    except OSError:
        raise MaintenanceTransitionStorageError("Failed to lstat root") from None

    if stat.S_ISLNK(st_before.st_mode):
        raise MaintenanceTransitionStorageError("root must not be a symlink")
    if not stat.S_ISDIR(st_before.st_mode):
        raise MaintenanceTransitionStorageError("root must be a directory")
    if st_before.st_uid != os.geteuid():
        raise MaintenanceTransitionStorageError("root owner mismatch")
    if (st_before.st_mode & 0o002) != 0:
        raise MaintenanceTransitionStorageError("root must not be world-writable")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        root_fd = os.open(str(root), flags)
    except (KeyboardInterrupt, SystemExit):
        raise
    except OSError:
        raise MaintenanceTransitionStorageError("Failed to open root descriptor") from None

    try:
        st_fd = os.fstat(root_fd)
        if (st_fd.st_dev, st_fd.st_ino) != (st_before.st_dev, st_before.st_ino):
            raise MaintenanceTransitionStorageError("root descriptor identity mismatch")
        if not stat.S_ISDIR(st_fd.st_mode) or stat.S_ISLNK(st_fd.st_mode):
            raise MaintenanceTransitionStorageError("root descriptor is not a directory")
        if st_fd.st_uid != os.geteuid():
            raise MaintenanceTransitionStorageError("root descriptor owner mismatch")
        if (st_fd.st_mode & 0o777) != (st_before.st_mode & 0o777):
            raise MaintenanceTransitionStorageError("root descriptor mode mismatch")
        if (st_fd.st_mode & 0o002) != 0:
            raise MaintenanceTransitionStorageError("root descriptor must not be world-writable")
    except (KeyboardInterrupt, SystemExit):
        os.close(root_fd)
        raise
    except Exception:
        os.close(root_fd)
        raise

    return root_fd


def _assert_no_stop_file(fd: int) -> None:
    try:
        os.stat("STOP", dir_fd=fd, follow_symlinks=False)
    except (KeyboardInterrupt, SystemExit):
        raise
    except FileNotFoundError:
        return
    except OSError:
        raise MaintenanceTransitionStorageError("Failed to stat STOP file") from None
    raise MaintenanceTransitionStorageError("STOP file detected")


def _check_stop_and_pending(root_fd: int, state_root_fd: int, state_root: Path) -> None:
    try:
        _assert_no_stop_file(root_fd)
        _assert_no_stop_file(state_root_fd)
    except (KeyboardInterrupt, SystemExit):
        raise
    except MaintenanceTransitionStorageError:
        raise
    except Exception:
        raise MaintenanceTransitionStorageError("STOP file check failed") from None
    try:
        assert_no_pending_cold_adoption_artifacts_locked(state_root)
    except (KeyboardInterrupt, SystemExit):
        raise
    except MaintenanceTransitionStorageError:
        raise
    except Exception:
        raise MaintenanceTransitionStorageError("Pending cold adoption artifacts check failed") from None


def _recheck_repository(root: Path, expected_norm_repo: dict[str, Any]) -> None:
    try:
        raw = validate_repository(root, require_clean=True)
        norm = validate_accepted_repository(raw)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise MaintenanceTransitionStorageError("Repository post-recheck validation failed") from None
    if not isinstance(norm, dict) or not _type_sensitive_equal(norm, expected_norm_repo):
        raise MaintenanceTransitionStorageError("Repository drift detected during hold")


def _recheck_checkpoint(
    state_root_fd: int,
    state_root: Path,
    expected_sha256: str,
    expected_repo: dict[str, Any],
) -> None:
    try:
        chk_obj, chk_sha = _load_and_audit_accepted_checkpoint(state_root_fd, state_root)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise MaintenanceTransitionStorageError("Checkpoint post-recheck load failed") from None
    if chk_sha != expected_sha256:
        raise MaintenanceTransitionStorageError("Checkpoint SHA drift detected during hold")
    chk_raw_repo = chk_obj.get("repository") if isinstance(chk_obj, dict) else None
    if not isinstance(chk_raw_repo, dict):
        raise MaintenanceTransitionStorageError("Checkpoint post-recheck missing repository dict")
    try:
        chk_norm = _validate_persisted_repository(chk_raw_repo)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise MaintenanceTransitionStorageError("Checkpoint post-recheck repository invalid") from None
    if not isinstance(chk_norm, dict) or not _type_sensitive_equal(chk_norm, expected_repo):
        raise MaintenanceTransitionStorageError("Checkpoint repository drift detected during hold")


def read_maintenance_transition_locked(
    root: Path,
    state_root: Path,
) -> dict[str, Any] | None:
    """Read, validate, and verify an existing reviewed maintenance-transition record.

    Caller holds existing exclusive Supervisor lock. Holds Broker client lock across all
    inspection and verification steps, pre/post checking STOP files and pending cold adoption artifacts.
    Returns the detached record dict, or None if the directory is missing or empty.
    """
    if not isinstance(root, Path) or not root.is_absolute():
        raise MaintenanceTransitionStorageError("root must be an absolute pathlib.Path")
    expected_state_root = root / _STATE_ROOT_REL
    if not isinstance(state_root, Path) or state_root != expected_state_root:
        raise MaintenanceTransitionStorageError("state_root must match canonical expected path")

    try:
        with hold_idle_broker_client_lock_for_cold_adoption(root):
            root_fd = _pin_validated_root(root)
            try:
                try:
                    state_root_fd = _open_validated_state_root(state_root)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionStorageError("Failed to open validated state_root") from None

                try:
                    _check_stop_and_pending(root_fd, state_root_fd, state_root)

                    try:
                        raw_repo = validate_repository(root, require_clean=True)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        raise MaintenanceTransitionStorageError("validate_repository failed") from None
                    if not isinstance(raw_repo, dict):
                        raise MaintenanceTransitionStorageError("validate_repository returned non-dict")

                    try:
                        norm_repo = validate_accepted_repository(raw_repo)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        raise MaintenanceTransitionStorageError("validate_accepted_repository failed") from None
                    if not isinstance(norm_repo, dict):
                        raise MaintenanceTransitionStorageError("validate_accepted_repository returned non-dict")

                    try:
                        chk_obj, chk_sha = _load_and_audit_accepted_checkpoint(state_root_fd, state_root)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        raise MaintenanceTransitionStorageError("Failed to load and audit accepted checkpoint") from None
                    if not isinstance(chk_obj, dict):
                        raise MaintenanceTransitionStorageError("Checkpoint object is not a dict")
                    if not isinstance(chk_sha, str) or not _HEX_64_RE.fullmatch(chk_sha):
                        raise MaintenanceTransitionStorageError("Checkpoint SHA256 is invalid")

                    chk_raw_repo = chk_obj.get("repository")
                    if not isinstance(chk_raw_repo, dict):
                        raise MaintenanceTransitionStorageError("Checkpoint missing repository dict")
                    try:
                        chk_repo = _validate_persisted_repository(chk_raw_repo)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        raise MaintenanceTransitionStorageError("Checkpoint repository invalid") from None
                    if not isinstance(chk_repo, dict):
                        raise MaintenanceTransitionStorageError("Checkpoint repository invalid")

                    if norm_repo.get("head") == chk_repo.get("head"):
                        raise MaintenanceTransitionStorageError("Current head matches checkpoint head")

                    if not _type_sensitive_equal(norm_repo.get("protected_refs"), chk_repo.get("protected_refs")):
                        raise MaintenanceTransitionStorageError("Protected refs mismatch between current and checkpoint")

                    curr_ctrl = norm_repo.get("control_blobs")
                    chk_ctrl = chk_repo.get("control_blobs")
                    if (
                        not isinstance(curr_ctrl, dict)
                        or not isinstance(chk_ctrl, dict)
                        or set(curr_ctrl.keys()) != set(chk_ctrl.keys())
                    ):
                        raise MaintenanceTransitionStorageError("Control blobs keys mismatch")

                    for k, v in chk_ctrl.items():
                        if k != _AUTOPILOT_CONTROL_PATH and curr_ctrl.get(k) != v:
                            raise MaintenanceTransitionStorageError("Control blob hash mismatch")

                    autopilot_differs = (curr_ctrl.get(_AUTOPILOT_CONTROL_PATH) != chk_ctrl.get(_AUTOPILOT_CONTROL_PATH))

                    if _type_sensitive_equal(norm_repo.get("assignment_scope"), chk_repo.get("assignment_scope")):
                        raise MaintenanceTransitionStorageError("Current assignment scope matches checkpoint scope")

                    try:
                        epoch_base = rescope_epoch_basename(norm_repo)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        raise MaintenanceTransitionStorageError("Failed to derive epoch basename") from None
                    if not isinstance(epoch_base, str) or not epoch_base:
                        raise MaintenanceTransitionStorageError("Invalid epoch basename derived")
                    expected_leaf = f"{epoch_base}.json"

                    try:
                        transitions_fd = open_maintenance_transitions_dir(state_root_fd, create=False)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception:
                        raise MaintenanceTransitionStorageError("Failed to open transitions directory") from None

                    if transitions_fd is None:
                        if autopilot_differs:
                            raise MaintenanceTransitionStorageError("Autopilot control changed without transition record")
                        _check_stop_and_pending(root_fd, state_root_fd, state_root)
                        _recheck_repository(root, norm_repo)
                        _recheck_checkpoint(state_root_fd, state_root, chk_sha, chk_repo)
                        return None

                    try:
                        try:
                            leaves = list_maintenance_transition_leaves(transitions_fd)
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception:
                            raise MaintenanceTransitionStorageError("Failed to list maintenance transition leaves") from None

                        if len(leaves) == 0:
                            if autopilot_differs:
                                raise MaintenanceTransitionStorageError("Autopilot control changed without transition record")
                            _check_stop_and_pending(root_fd, state_root_fd, state_root)
                            _recheck_repository(root, norm_repo)
                            _recheck_checkpoint(state_root_fd, state_root, chk_sha, chk_repo)
                            return None

                        if len(leaves) != 1 or leaves[0] != expected_leaf:
                            raise MaintenanceTransitionStorageError("Unexpected or foreign leaf entries in transitions directory")

                        try:
                            raw_record = read_maintenance_transition_bytes(transitions_fd, expected_leaf)
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception:
                            raise MaintenanceTransitionStorageError("Failed to read leaf bytes") from None

                        if raw_record is None:
                            raise MaintenanceTransitionStorageError("Leaf bytes missing")

                        try:
                            record = parse_maintenance_transition_record(raw_record)
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception:
                            raise MaintenanceTransitionStorageError("Failed to parse maintenance transition record") from None

                        if not isinstance(record, dict):
                            raise MaintenanceTransitionStorageError("Record is not a dict")

                        if record.get("prior_checkpoint_sha256") != chk_sha:
                            raise MaintenanceTransitionStorageError("Record prior_checkpoint_sha256 mismatch")
                        if record.get("prior_head") != chk_repo.get("head"):
                            raise MaintenanceTransitionStorageError("Record prior_head mismatch")
                        if not _type_sensitive_equal(record.get("new_repository"), norm_repo):
                            raise MaintenanceTransitionStorageError("Record new_repository does not match current repository")

                        if autopilot_differs:
                            declared = any(
                                _AUTOPILOT_CONTROL_PATH in c.get("paths", [])
                                for c in record.get("maintenance_commits", [])
                                if isinstance(c, dict)
                            )
                            if not declared:
                                raise MaintenanceTransitionStorageError("Autopilot control change not declared in commits")

                        try:
                            verify_declared_maintenance_git_chain(root, record)
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception:
                            raise MaintenanceTransitionStorageError("verify_declared_maintenance_git_chain failed") from None

                        _check_stop_and_pending(root_fd, state_root_fd, state_root)
                        _recheck_repository(root, norm_repo)
                        _recheck_checkpoint(state_root_fd, state_root, chk_sha, chk_repo)

                        try:
                            post_raw_record = read_maintenance_transition_bytes(transitions_fd, expected_leaf)
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception:
                            raise MaintenanceTransitionStorageError("Failed to restat leaf bytes") from None

                        if post_raw_record != raw_record:
                            raise MaintenanceTransitionStorageError("Leaf bytes drift detected")

                        return copy.deepcopy(record)
                    finally:
                        os.close(transitions_fd)
                finally:
                    os.close(state_root_fd)
            finally:
                os.close(root_fd)
    except (KeyboardInterrupt, SystemExit):
        raise
    except MaintenanceTransitionStorageError:
        raise
    except Exception:
        raise MaintenanceTransitionStorageError("Broker lock operation failed") from None
