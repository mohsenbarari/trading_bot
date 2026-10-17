#!/usr/bin/env python3
"""Reviewed maintenance-transition startup verification and wiring declarations."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from scripts.two_site_codex_autopilot import (
    AutopilotError,
    validate_accepted_repository,
    validate_repository,
)
from scripts.two_site_root_maintenance_bridge import (
    RootMaintenanceBridgeError,
    read_root_maintenance_bridge_locked,
)
from scripts.two_site_maintenance_transition_storage import (
    MainMaintenanceRouteError,
    read_maintenance_transition_locked,
)

STATE_ROOT_REL = Path(".local/two-site-refactor/autopilot")
STOP_FILENAME = "STOP"


class MaintenanceTransitionStartupError(AutopilotError):
    """Raised when reviewed maintenance transition startup verification fails."""


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


def require_reviewed_maintenance_transition_locked(
    root: Path,
    state_root: Path,
    repository: object,
) -> None:
    """Verify an accepted maintenance transition record for startup or monitor preflight.

    Caller holds exclusive Supervisor lock. The explicit live-Scope Root route is checked
    first; marked errors never fall back. Each reader holds the idle Broker client lock.
    After Broker exit, live repository is revalidated and both STOP paths are rechecked.
    Does not perform any disk writes.
    """
    if not isinstance(root, Path) or not root.is_absolute():
        raise MaintenanceTransitionStartupError("root must be an absolute pathlib.Path")
    if not isinstance(state_root, Path) or state_root != root / STATE_ROOT_REL:
        raise MaintenanceTransitionStartupError("state_root must match canonical expected path")

    try:
        record = read_root_maintenance_bridge_locked(root, state_root, repository)
    except (KeyboardInterrupt, SystemExit):
        raise
    except RootMaintenanceBridgeError:
        raise MaintenanceTransitionStartupError("root maintenance bridge verification failed") from None

    if record is None:
        try:
            record = read_maintenance_transition_locked(root, state_root)
        except (KeyboardInterrupt, SystemExit):
            raise
        except MainMaintenanceRouteError:
            raise MaintenanceTransitionStartupError("main maintenance verification failed") from None
        except Exception:
            record = None

        if record is None:
            try:
                from scripts.two_site_quarantine_recovery_storage import (
                    read_quarantine_recovery_locked,
                )

                envelope = read_quarantine_recovery_locked(root, state_root)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                raise MaintenanceTransitionStartupError(
                    "quarantine recovery verification failed"
                ) from exc

            if not isinstance(envelope, dict):
                raise MaintenanceTransitionStartupError(
                    "no reviewed maintenance transition or quarantine recovery record found"
                )
            rec = envelope.get("record")
            if not isinstance(rec, dict) or not isinstance(rec.get("new_repository"), dict):
                raise MaintenanceTransitionStartupError(
                    "invalid quarantine recovery record"
                )
            record = rec
        elif not isinstance(record, dict):
            raise MaintenanceTransitionStartupError("invalid maintenance transition record")
    elif type(record) is not dict:
        raise MaintenanceTransitionStartupError("invalid root maintenance bridge record")

    try:
        norm_current = validate_accepted_repository(repository)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise MaintenanceTransitionStartupError("supplied repository validation failed") from exc

    record_repo = record.get("new_repository")
    if not isinstance(record_repo, dict) or not _type_sensitive_equal(record_repo, norm_current):
        raise MaintenanceTransitionStartupError(
            "record new_repository does not match current repository"
        )

    try:
        live_raw = validate_repository(root, require_clean=True)
        live_norm = validate_accepted_repository(live_raw)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise MaintenanceTransitionStartupError("live repository revalidation failed") from exc

    if not _type_sensitive_equal(live_norm, norm_current):
        raise MaintenanceTransitionStartupError(
            "repository drift detected after broker lock release"
        )

    for stop_path in (root / STOP_FILENAME, state_root / STOP_FILENAME):
        try:
            if os.path.lexists(stop_path):
                raise MaintenanceTransitionStartupError("STOP file detected")
        except MaintenanceTransitionStartupError:
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise MaintenanceTransitionStartupError("STOP check failed") from exc


WIRING_REPLACEMENTS: tuple[tuple[str, str, str], ...] = (
    (
        "scripts/two_site_codex_autopilot.py",
        (
            '        if root is None or len(prior_epoch_names) != 1 or current_epoch_exists:\n'
            '            raise AutopilotError("prior epoch requires matching accepted checkpoint")'
        ),
        (
            '        if root is None:\n'
            '            raise AutopilotError("prior epoch requires matching accepted checkpoint")\n'
            '        if len(prior_epoch_names) > 1 or current_epoch_exists:\n'
            '            try:\n'
            '                from scripts.two_site_maintenance_transition_startup import (\n'
            '                    require_reviewed_maintenance_transition_locked,\n'
            '                )\n'
            '                require_reviewed_maintenance_transition_locked(root, state_root, repository)\n'
            '                return\n'
            '            except (KeyboardInterrupt, SystemExit):\n'
            '                raise\n'
            '            except Exception as exc:\n'
            '                raise AutopilotError(\n'
            '                    "prior epoch requires matching accepted checkpoint"\n'
            '                ) from exc'
        ),
    ),
    (
        "scripts/two_site_autopilot_monitor_preflight.py",
        (
            '            try:\n'
            '                checkpoint = _load_accepted_checkpoint(state_root)\n'
            '                if checkpoint is None or not _accepted_checkpoint_matches_repository(checkpoint, repo):\n'
            '                    return False, "STALE_CHECKPOINT"\n'
            '            except Exception:\n'
            '                return False, "STALE_CHECKPOINT"'
        ),
        (
            '            try:\n'
            '                checkpoint = _load_accepted_checkpoint(state_root)\n'
            '                if checkpoint is None or not _accepted_checkpoint_matches_repository(checkpoint, repo):\n'
            '                    from scripts.two_site_maintenance_transition_startup import (\n'
            '                        require_reviewed_maintenance_transition_locked,\n'
            '                    )\n'
            '                    require_reviewed_maintenance_transition_locked(root, state_root, repo)\n'
            '            except Exception:\n'
            '                return False, "STALE_CHECKPOINT"'
        ),
    ),
    (
        "scripts/two_site_autopilot_monitor_preflight.py",
        (
            '            try:\n'
            '                cp_recheck = _load_accepted_checkpoint(state_root)\n'
            '                if cp_recheck is None or not _accepted_checkpoint_matches_repository(cp_recheck, repo_recheck):\n'
            '                    return False, "STALE_CHECKPOINT"\n'
            '            except Exception:\n'
            '                return False, "STALE_CHECKPOINT"'
        ),
        (
            '            try:\n'
            '                cp_recheck = _load_accepted_checkpoint(state_root)\n'
            '                if cp_recheck is None or not _accepted_checkpoint_matches_repository(cp_recheck, repo_recheck):\n'
            '                    from scripts.two_site_maintenance_transition_startup import (\n'
            '                        require_reviewed_maintenance_transition_locked,\n'
            '                    )\n'
            '                    require_reviewed_maintenance_transition_locked(root, state_root, repo_recheck)\n'
            '            except Exception:\n'
            '                return False, "STALE_CHECKPOINT"'
        ),
    ),
)

__all__ = [
    "MaintenanceTransitionStartupError",
    "require_reviewed_maintenance_transition_locked",
    "WIRING_REPLACEMENTS",
]
