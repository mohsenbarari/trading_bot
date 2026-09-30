"""Dormant pure reviewed maintenance-transition publisher under AE03.

Provides atomic, one-shot, no-replace publication of reviewed maintenance-transition
records within an exclusive Broker client lock under Supervisor hold.
Validates live repository, persisted checkpoint, Git ancestry, and enforces strict
pre- and post-publication checks.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import stat
import sys
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
    MaintenanceTransitionAmbiguousError,
    publish_maintenance_transition_bytes_noreplace,
    read_maintenance_transition_bytes,
)
from scripts.two_site_maintenance_transition_git import (
    verify_declared_maintenance_git_chain,
)
from scripts.two_site_maintenance_transition_record import (
    build_maintenance_transition_record,
    serialize_maintenance_transition_record,
)
from scripts.two_site_codex_autopilot import (
    _validate_persisted_repository,
    rescope_epoch_basename,
    validate_accepted_repository,
    validate_repository,
)
from scripts.two_site_maintenance_transition_storage import (
    _check_stop_and_pending,
    _pin_validated_root,
    _recheck_checkpoint,
    _recheck_repository,
    _type_sensitive_equal,
)

_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_AUTOPILOT_CONTROL_PATH = "scripts/two_site_codex_autopilot.py"
_STATE_ROOT_REL = Path(".local/two-site-refactor/autopilot")


class MaintenanceTransitionPublishError(Exception):
    """Raised when maintenance transition pre-publication validation or setup fails."""


__all__ = [
    "MaintenanceTransitionAmbiguousError",
    "MaintenanceTransitionPublishError",
    "publish_maintenance_transition_locked",
]


def publish_maintenance_transition_locked(
    root: Path,
    state_root: Path,
    maintenance_commits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Publish a reviewed maintenance-transition record with one-shot no-replace semantics.

    PRECONDITION: Caller already holds existing exclusive Supervisor lock for
    the entire function (do not acquire a second copy).
    Holds Broker client lock across all prechecks, publication, and postchecks.
    Returns detached record dict ONLY after Broker exits successfully.
    """
    if not isinstance(root, Path) or not root.is_absolute():
        raise MaintenanceTransitionPublishError("root must be an absolute pathlib.Path")
    expected_state_root = root / _STATE_ROOT_REL
    if not isinstance(state_root, Path) or state_root != expected_state_root:
        raise MaintenanceTransitionPublishError("state_root must match canonical expected path")
    if not isinstance(maintenance_commits, list):
        raise MaintenanceTransitionPublishError("maintenance_commits must be a list")

    attempted = False
    record: dict[str, Any] | None = None

    try:
        with hold_idle_broker_client_lock_for_cold_adoption(root):
            root_fd: int | None = None
            state_root_fd: int | None = None
            trans_fd: int | None = None

            try:
                try:
                    root_fd = _pin_validated_root(root)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Failed to validate and pin root directory") from None

                try:
                    state_root_fd = _open_validated_state_root(state_root)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Failed to open validated state_root") from None

                try:
                    _check_stop_and_pending(root_fd, state_root_fd, state_root)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("STOP file or pending cold adoption artifacts detected") from None

                try:
                    raw_repo = validate_repository(root, require_clean=True)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("validate_repository failed") from None
                if not isinstance(raw_repo, dict):
                    raise MaintenanceTransitionPublishError("validate_repository returned non-dict")

                try:
                    norm_repo = validate_accepted_repository(raw_repo)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("validate_accepted_repository failed") from None
                if not isinstance(norm_repo, dict):
                    raise MaintenanceTransitionPublishError("validate_accepted_repository returned non-dict")

                try:
                    chk_obj, chk_sha = _load_and_audit_accepted_checkpoint(state_root_fd, state_root)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Failed to load and audit accepted checkpoint") from None
                if not isinstance(chk_obj, dict):
                    raise MaintenanceTransitionPublishError("Checkpoint object is not a dict")
                if not isinstance(chk_sha, str) or not _HEX_64_RE.fullmatch(chk_sha):
                    raise MaintenanceTransitionPublishError("Checkpoint SHA256 is invalid")

                chk_raw_repo = chk_obj.get("repository")
                if not isinstance(chk_raw_repo, dict):
                    raise MaintenanceTransitionPublishError("Checkpoint missing repository dict")
                try:
                    chk_repo = _validate_persisted_repository(chk_raw_repo)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Checkpoint repository invalid") from None
                if not isinstance(chk_repo, dict):
                    raise MaintenanceTransitionPublishError("Checkpoint repository invalid")

                if norm_repo.get("head") == chk_repo.get("head"):
                    raise MaintenanceTransitionPublishError("Current head matches checkpoint head")

                if not _type_sensitive_equal(norm_repo.get("protected_refs"), chk_repo.get("protected_refs")):
                    raise MaintenanceTransitionPublishError("Protected refs mismatch between current and checkpoint")

                curr_ctrl = norm_repo.get("control_blobs")
                chk_ctrl = chk_repo.get("control_blobs")
                if (
                    not isinstance(curr_ctrl, dict)
                    or not isinstance(chk_ctrl, dict)
                    or set(curr_ctrl.keys()) != set(chk_ctrl.keys())
                ):
                    raise MaintenanceTransitionPublishError("Control blobs keys mismatch")

                for k, v in chk_ctrl.items():
                    if k != _AUTOPILOT_CONTROL_PATH and curr_ctrl.get(k) != v:
                        raise MaintenanceTransitionPublishError("Control blob hash mismatch")

                autopilot_differs = (curr_ctrl.get(_AUTOPILOT_CONTROL_PATH) != chk_ctrl.get(_AUTOPILOT_CONTROL_PATH))

                if _type_sensitive_equal(norm_repo.get("assignment_scope"), chk_repo.get("assignment_scope")):
                    raise MaintenanceTransitionPublishError("Current assignment scope matches checkpoint scope")

                if autopilot_differs:
                    declared = any(
                        _AUTOPILOT_CONTROL_PATH in c.get("paths", [])
                        for c in maintenance_commits
                        if isinstance(c, dict)
                    )
                    if not declared:
                        raise MaintenanceTransitionPublishError("Autopilot control change not declared in commits")

                try:
                    record = build_maintenance_transition_record(
                        prior_checkpoint_sha256=chk_sha,
                        prior_head=chk_repo["head"],
                        new_repository=norm_repo,
                        maintenance_commits=maintenance_commits,
                    )
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Failed to build maintenance transition record") from None

                try:
                    raw_record_bytes = serialize_maintenance_transition_record(record)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Failed to serialize maintenance transition record") from None

                try:
                    epoch_base = rescope_epoch_basename(norm_repo)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Failed to derive epoch basename") from None
                if not isinstance(epoch_base, str) or not epoch_base:
                    raise MaintenanceTransitionPublishError("Invalid epoch basename derived")
                expected_leaf = f"{epoch_base}.json"

                try:
                    verify_declared_maintenance_git_chain(root, record)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("verify_declared_maintenance_git_chain failed") from None

                try:
                    trans_fd = open_maintenance_transitions_dir(state_root_fd, create=True)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Failed to open or create maintenance-transitions directory") from None
                if trans_fd is None:
                    raise MaintenanceTransitionPublishError("Failed to obtain transitions directory descriptor")

                try:
                    existing_leaves = list_maintenance_transition_leaves(trans_fd)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Failed to list maintenance transition leaves") from None

                if len(existing_leaves) > 0:
                    raise MaintenanceTransitionPublishError("Transitions directory is not empty; existing transitions block publication")

                try:
                    os.stat(expected_leaf, dir_fd=trans_fd, follow_symlinks=False)
                    leaf_exists = True
                except FileNotFoundError:
                    leaf_exists = False
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Failed to stat leaf before publication") from None

                if leaf_exists:
                    raise MaintenanceTransitionPublishError("Final transition leaf already exists")

                try:
                    _check_stop_and_pending(root_fd, state_root_fd, state_root)
                    _recheck_repository(root, norm_repo)
                    _recheck_checkpoint(state_root_fd, state_root, chk_sha, chk_repo)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise MaintenanceTransitionPublishError("Pre-publication recheck of STOP, pending, repository, or checkpoint failed") from None

                attempted = True

                publish_maintenance_transition_bytes_noreplace(trans_fd, expected_leaf, raw_record_bytes)

                try:
                    post_raw = read_maintenance_transition_bytes(trans_fd, expected_leaf)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    raise MaintenanceTransitionAmbiguousError("Failed to read leaf bytes after publication") from exc

                if post_raw != raw_record_bytes:
                    raise MaintenanceTransitionAmbiguousError("Leaf bytes mismatch on post-publish verification")

                try:
                    leaves_after = list_maintenance_transition_leaves(trans_fd)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    raise MaintenanceTransitionAmbiguousError("Failed to list leaves after publication") from exc

                if leaves_after != [expected_leaf]:
                    raise MaintenanceTransitionAmbiguousError("Expected exactly one published leaf after publish")

                try:
                    _check_stop_and_pending(root_fd, state_root_fd, state_root)
                    _recheck_repository(root, norm_repo)
                    _recheck_checkpoint(state_root_fd, state_root, chk_sha, chk_repo)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    raise MaintenanceTransitionAmbiguousError("Post-publication recheck of STOP, pending, repository, or checkpoint failed") from exc

            finally:
                first_close_exc: Exception | None = None
                for fd_val in (trans_fd, state_root_fd, root_fd):
                    if fd_val is not None:
                        try:
                            os.close(fd_val)
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception as exc:
                            if first_close_exc is None:
                                first_close_exc = exc

                pending_err = sys.exc_info()[1]
                if pending_err is None and first_close_exc is not None:
                    if attempted:
                        raise MaintenanceTransitionAmbiguousError(
                            "Descriptor close failed after publication attempt"
                        ) from first_close_exc
                    raise MaintenanceTransitionPublishError("Descriptor close failed") from None

    except (KeyboardInterrupt, SystemExit) as exc:
        if attempted:
            raise MaintenanceTransitionAmbiguousError(
                "Interrupted after publication attempt; publication is ambiguous"
            ) from exc
        raise
    except MaintenanceTransitionAmbiguousError:
        raise
    except Exception as exc:
        if attempted:
            raise MaintenanceTransitionAmbiguousError(
                "Failure after publication attempt; publication is ambiguous"
            ) from exc
        raise MaintenanceTransitionPublishError(
            "Pre-publication validation or setup failed"
        ) from None

    if record is None:
        raise MaintenanceTransitionPublishError("Record was not built")

    return copy.deepcopy(record)
