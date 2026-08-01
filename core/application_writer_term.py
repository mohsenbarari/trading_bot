"""Fail-closed local admission for a Writer Witness term.

The Writer Witness service is the authority that grants a site a short-lived
writer term.  A root-owned host agent materializes the currently granted term
as a local lease file.  Application processes never contact the Witness and
never elect a writer: they only prove that the local lease is safe, current,
and belongs to their configured site.

This module is deliberately default-off so legacy deployments preserve their
behaviour.  A release that enables it must also disable implicit schema
bootstrap and run as an explicit single-writer runtime.  Those static checks
are kept separate from the live lease read so callers can reject a bad runtime
configuration before opening a database or creating a Bot client.
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
    """Raised when a term-enforced process cannot prove local writer authority."""


DEFAULT_SAFETY_MARGIN_SECONDS = 5
DEFAULT_MAX_LEASE_DURATION_SECONDS = 90
MIN_SAFETY_MARGIN_SECONDS = 1
MAX_SAFETY_MARGIN_SECONDS = 60
MIN_MAX_LEASE_DURATION_SECONDS = 2
MAX_MAX_LEASE_DURATION_SECONDS = 300


@dataclass(frozen=True)
class ApplicationWriterTermPolicy:
    """Policy used at each application-side writer boundary.

    ``owner_uid`` is intentionally not configurable through environment
    settings.  Production lease files are owned by root and the application
    container consumes a read-only mount of their parent directory.
    """

    enabled: bool = False
    local_site: str | None = None
    lease_file: Path | None = None
    owner_uid: int = 0
    safety_margin_seconds: int = DEFAULT_SAFETY_MARGIN_SECONDS
    max_lease_duration_seconds: int = DEFAULT_MAX_LEASE_DURATION_SECONDS


@dataclass(frozen=True)
class ValidatedWriterTerm:
    """Non-secret, validated identity of the currently active writer term."""

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
    """Project Settings into a policy without importing config or doing I/O."""

    enabled = getattr(settings, "application_writer_term_enforced", False)
    if enabled is False:
        # Do not inspect optional term settings on a legacy/default runtime.
        return ApplicationWriterTermPolicy()
    if enabled is not True:
        raise ApplicationWriterTermError("writer term enforcement setting is invalid")

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
        enabled=True,
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


def _site_for_server_mode(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"foreign", "germany", "german", "de"}:
        return "webapp_fi"
    if normalized in {"iran", "ir"}:
        return "webapp_ir"
    return None


def validate_application_writer_term_runtime(
    settings: object,
    *,
    expected_service: str | None = None,
) -> ApplicationWriterTermPolicy:
    """Validate static invariants before an app or bot starts side effects.

    The function intentionally does not read the lease file.  Call
    :func:`require_active_writer_term` immediately afterwards for the live
    process-start proof.  Keeping bootstrap disabled is critical: an app
    start must never run ``create_all`` merely because it has acquired a term.
    """

    policy = policy_from_settings(settings)
    if policy.enabled is False:
        return policy

    local_site, _lease_file, _owner_uid, _margin, _duration = _enabled_policy_values(policy)
    if getattr(settings, "single_writer_runtime_enabled", False) is not True:
        raise ApplicationWriterTermError(
            "SINGLE_WRITER_RUNTIME_ENABLED must be true when Writer Witness enforcement is enabled"
        )
    if getattr(settings, "database_schema_bootstrap_enabled", True) is not False:
        raise ApplicationWriterTermError(
            "DATABASE_SCHEMA_BOOTSTRAP_ENABLED must be false when Writer Witness enforcement is enabled"
        )
    expected_site = _site_for_server_mode(getattr(settings, "server_mode", None))
    if expected_site is None or expected_site != local_site:
        raise ApplicationWriterTermError(
            "writer term local site does not match SERVER_MODE"
        )
    if expected_service is not None:
        configured_service = str(getattr(settings, "trading_bot_service", "") or "").strip().lower()
        if configured_service != expected_service:
            raise ApplicationWriterTermError(
                f"TRADING_BOT_SERVICE must be {expected_service!r} when Writer Witness enforcement is enabled"
            )
    return policy


def _validate_controlled_directory(info: os.stat_result, *, owner_uid: int) -> None:
    if not stat.S_ISDIR(info.st_mode):
        raise ApplicationWriterTermError("writer term lease ancestor is not a directory")
    if info.st_uid not in {0, owner_uid}:
        raise ApplicationWriterTermError("writer term lease ancestor is not owner controlled")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
        raise ApplicationWriterTermError(
            "writer term lease ancestor is writable by an unsafe principal"
        )


def _validate_lease_ancestors(path: Path, *, owner_uid: int) -> None:
    """Reject symlinked or uncontrolled parents before opening the lease leaf."""

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
    """Require a current lease for the configured local site, or fail closed."""

    if not isinstance(policy, ApplicationWriterTermPolicy):
        raise ApplicationWriterTermError("writer term policy is invalid")
    if policy.enabled is False:
        return None
    if policy.enabled is not True:
        raise ApplicationWriterTermError("writer term policy enabled flag is invalid")

    local_site, lease_file, owner_uid, safety_margin, max_duration = _enabled_policy_values(policy)
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
    if lease.expires_at - lease.issued_at > timedelta(seconds=max_duration):
        raise ApplicationWriterTermError("writer term duration exceeds the configured maximum")
    if lease.expires_at <= observed_at + timedelta(seconds=safety_margin):
        raise ApplicationWriterTermError("writer term expires within the required safety margin")

    return ValidatedWriterTerm(
        holder_site=lease.holder_site,
        writer_epoch=lease.writer_epoch,
        lease_id=lease.lease_id,
        issued_at=lease.issued_at,
        expires_at=lease.expires_at,
        witness_transition_id=lease.witness_transition_id,
    )
