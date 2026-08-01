"""Local, default-off application write gate for Writer Witness terms.

This module validates a host-level lease immediately before an application
mutation.  It remains default-off; :mod:`core.db` and :mod:`main` project its
explicit settings and call it at their write boundaries.  It does not contact
the Witness or make any database changes itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import stat

from core.production_writer_lease import (
    WEBAPP_SITES,
    ProductionWriterLeaseError,
    load_production_writer_lease,
)


class ApplicationWriterTermError(RuntimeError):
    """Raised when an enabled application writer term cannot be proved."""


DEFAULT_SAFETY_MARGIN_SECONDS = 5
DEFAULT_MAX_LEASE_DURATION_SECONDS = 90
MIN_SAFETY_MARGIN_SECONDS = 1
MAX_SAFETY_MARGIN_SECONDS = 60
MIN_MAX_LEASE_DURATION_SECONDS = 2
MAX_MAX_LEASE_DURATION_SECONDS = 300


@dataclass(frozen=True)
class ApplicationWriterTermPolicy:
    """Explicit policy for a prospective application write boundary.

    ``enabled`` defaults to ``False`` so adding this primitive has no runtime
    effect until a future integration opts in.  When enabled, ``local_site``
    and ``lease_file`` are mandatory and the lease must name the local site as
    its active holder.  The owner expectation defaults to root (UID 0).
    """

    enabled: bool = False
    local_site: str | None = None
    lease_file: Path | None = None
    owner_uid: int = 0
    safety_margin_seconds: int = DEFAULT_SAFETY_MARGIN_SECONDS
    max_lease_duration_seconds: int = DEFAULT_MAX_LEASE_DURATION_SECONDS


@dataclass(frozen=True)
class ValidatedWriterTerm:
    """Non-secret details of an active term safe for a caller to retain."""

    holder_site: str
    writer_epoch: int
    lease_id: str
    issued_at: datetime
    expires_at: datetime
    witness_transition_id: str


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ApplicationWriterTermError("writer term clock must be timezone-aware")
    return now.astimezone(timezone.utc)


def _enabled_policy_values(
    policy: ApplicationWriterTermPolicy,
) -> tuple[str, Path, int, int, int]:
    if not isinstance(policy.local_site, str) or policy.local_site not in WEBAPP_SITES:
        raise ApplicationWriterTermError("writer term local site is invalid")
    if not isinstance(policy.lease_file, Path):
        raise ApplicationWriterTermError("writer term lease file is required")
    if not policy.lease_file.is_absolute():
        raise ApplicationWriterTermError("writer term lease file must be an absolute path")
    if type(policy.owner_uid) is not int or policy.owner_uid < 0:
        raise ApplicationWriterTermError("writer term owner uid is invalid")
    if (
        type(policy.safety_margin_seconds) is not int
        or not MIN_SAFETY_MARGIN_SECONDS
        <= policy.safety_margin_seconds
        <= MAX_SAFETY_MARGIN_SECONDS
    ):
        raise ApplicationWriterTermError("writer term safety margin is invalid")
    if (
        type(policy.max_lease_duration_seconds) is not int
        or not MIN_MAX_LEASE_DURATION_SECONDS
        <= policy.max_lease_duration_seconds
        <= MAX_MAX_LEASE_DURATION_SECONDS
        or policy.max_lease_duration_seconds <= policy.safety_margin_seconds
    ):
        raise ApplicationWriterTermError("writer term maximum duration is invalid")
    return (
        policy.local_site,
        policy.lease_file,
        policy.owner_uid,
        policy.safety_margin_seconds,
        policy.max_lease_duration_seconds,
    )


def policy_from_settings(settings: object) -> ApplicationWriterTermPolicy:
    """Project application settings into a term policy without importing config.

    The default-off path deliberately reads only the enforcement flag and
    therefore does not touch a term path or the filesystem.  When enabled,
    the owner is unconditionally root; deployment configuration cannot select
    another owner.  The host lease agent atomically replaces the lease file,
    so a future Compose integration must bind-mount its root-owned *parent
    directory* read-only, never only the leaf file, to avoid retaining a stale
    inode inside the container.
    """

    enabled = getattr(settings, "application_writer_term_enforced", False)
    if enabled is False:
        return ApplicationWriterTermPolicy()

    raw_lease_file = getattr(settings, "application_writer_term_lease_file", None)
    if raw_lease_file is None or raw_lease_file == "":
        lease_file = None
    elif isinstance(raw_lease_file, Path):
        lease_file = raw_lease_file
    elif isinstance(raw_lease_file, str):
        lease_file = Path(raw_lease_file)
    else:
        raise ApplicationWriterTermError("writer term lease file setting is invalid")

    return ApplicationWriterTermPolicy(
        enabled=enabled,
        local_site=getattr(settings, "application_writer_term_local_site", None),
        lease_file=lease_file,
        owner_uid=0,
        safety_margin_seconds=getattr(
            settings,
            "application_writer_term_safety_margin_seconds",
            DEFAULT_SAFETY_MARGIN_SECONDS,
        ),
        max_lease_duration_seconds=getattr(
            settings,
            "application_writer_term_max_lease_duration_seconds",
            DEFAULT_MAX_LEASE_DURATION_SECONDS,
        ),
    )


def _validate_controlled_directory(info: os.stat_result, *, owner_uid: int) -> None:
    if not stat.S_ISDIR(info.st_mode):
        raise ApplicationWriterTermError("writer term lease ancestor is not a directory")
    if info.st_uid not in {0, owner_uid}:
        raise ApplicationWriterTermError("writer term lease ancestor is not owner controlled")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
        raise ApplicationWriterTermError("writer term lease ancestor is writable by an unsafe principal")


def _validate_lease_ancestors(path: Path, *, owner_uid: int) -> None:
    """Reject symlink or uncontrolled ancestors before opening the lease leaf.

    Each component is opened relative to the previous directory descriptor with
    ``O_NOFOLLOW``.  Controlled ancestors make the subsequent secure leaf open
    stable: root-owned non-writable directories are accepted, as is a sticky
    root-owned parent such as ``/tmp`` when every following component is still
    controlled.  This keeps test fixtures portable without weakening the
    root-owned production default.
    """

    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise ApplicationWriterTermError("writer term lease ancestor validation is unavailable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow | directory
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        _validate_controlled_directory(os.fstat(descriptor), owner_uid=owner_uid)
        for component in path.parts[1:-1]:
            if component in {"", ".", ".."}:
                raise ApplicationWriterTermError("writer term lease path is not canonical")
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            _validate_controlled_directory(os.fstat(descriptor), owner_uid=owner_uid)
    except ApplicationWriterTermError:
        raise
    except OSError as exc:
        raise ApplicationWriterTermError("writer term lease ancestors are unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def require_active_writer_term(
    policy: ApplicationWriterTermPolicy,
    *,
    now: datetime | None = None,
) -> ValidatedWriterTerm | None:
    """Permit a write only when the enabled local Writer Witness term is active.

    An explicit disabled policy returns ``None`` without accessing the lease
    file.  An enabled policy fails closed for absent, relative, insecure,
    malformed, future, expired, or wrong-holder leases.  ``now`` is injectable
    for deterministic tests and must be timezone-aware when supplied.
    """

    if not isinstance(policy, ApplicationWriterTermPolicy):
        raise ApplicationWriterTermError("writer term policy is invalid")
    if policy.enabled is False:
        return None
    if policy.enabled is not True:
        raise ApplicationWriterTermError("writer term policy enabled flag is invalid")

    (
        local_site,
        lease_file,
        owner_uid,
        safety_margin_seconds,
        max_lease_duration_seconds,
    ) = _enabled_policy_values(policy)
    observed_at = _utc_now(now)
    _validate_lease_ancestors(lease_file, owner_uid=owner_uid)
    try:
        lease = load_production_writer_lease(lease_file, owner_uid=owner_uid)
    except ProductionWriterLeaseError as exc:
        raise ApplicationWriterTermError("writer term lease is unavailable or unsafe") from exc
    except Exception as exc:
        raise ApplicationWriterTermError("writer term lease cannot be validated") from exc

    if lease.holder_site != local_site:
        raise ApplicationWriterTermError("writer term holder does not match the local site")
    if lease.issued_at > observed_at:
        raise ApplicationWriterTermError("writer term is not active yet")
    if lease.expires_at <= observed_at:
        raise ApplicationWriterTermError("writer term is expired")
    if lease.expires_at - lease.issued_at > timedelta(seconds=max_lease_duration_seconds):
        raise ApplicationWriterTermError("writer term duration exceeds the configured maximum")
    if lease.expires_at <= observed_at + timedelta(seconds=safety_margin_seconds):
        raise ApplicationWriterTermError("writer term expires within the required safety margin")

    return ValidatedWriterTerm(
        holder_site=lease.holder_site,
        writer_epoch=lease.writer_epoch,
        lease_id=lease.lease_id,
        issued_at=lease.issued_at,
        expires_at=lease.expires_at,
        witness_transition_id=lease.witness_transition_id,
    )
