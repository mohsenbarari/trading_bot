"""Root-owned, term-scoped gate for effectful background execution.

The Writer Witness lease says which WebApp site may write.  It does *not* by
itself say whether an interrupted external effect (for example a Telegram
delivery) was reconciled before a new writer started.  This small local gate
therefore requires a separate, short-lived, root-owned authorization before a
selected worker may claim, recover, send, or retry an external effect.

The authorization is deliberately a local receipt, not an authority source:

* it is default-off;
* it binds one exact, already-validated Writer Witness term;
* it carries an explicit ``reconciliation_complete_no_resend`` decision and
  opaque evidence hash; and
* this module never contacts the Witness, promotes a site, sends a provider
  request, performs reconciliation, or writes an authorization on its own.

``write_external_effect_execution_authorization`` is an explicit root-side
installation primitive for a reviewed controller/operator.  Runtime callers
only read the authorization.  A later production control-plane integration
must decide and evidence reconciliation before invoking that writer; it must
not treat this module as proof that reconciliation occurred.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

from core.application_writer_term import ValidatedWriterTerm
from core.production_writer_lease import WEBAPP_SITES


EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_SCHEMA = "external-effect-execution-authorization-v1"
MAX_EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_BYTES = 16 * 1024

# The decision is intentionally narrow.  There is no broad "continue",
# "retry everything", or wildcard decision that could quietly re-enable old
# outbound work after a term transition.
RECONCILIATION_DECISION_COMPLETE_NO_RESEND = "reconciliation_complete_no_resend"

# These names are deliberately explicit rather than a wildcard.  Adding a new
# effectful worker requires a code review and a corresponding authorization
# scope before it can run under enforcement.
EXTERNAL_EFFECT_SCOPE_TRADE_WEBAPP_DELIVERY = "trade_webapp_delivery"
EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY = "trade_telegram_delivery"
EXTERNAL_EFFECT_SCOPE_TELEGRAM_ADMIN_BROADCAST_DELIVERY = "telegram_admin_broadcast_delivery"
EXTERNAL_EFFECT_SCOPE_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY = "telegram_notification_outbox_delivery"
EXTERNAL_EFFECT_SCOPE_OFFER_TELEGRAM_PUBLICATION = "offer_telegram_publication"
EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_RUNTIME = "telegram_bot_runtime"
# Remaining provider-visible P0 surfaces are deliberately split by delivery
# domain.  They are not wildcard permissions for arbitrary HTTP clients.
EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT = "telegram_bot_api_effect"
EXTERNAL_EFFECT_SCOPE_TELEGRAM_DIRECT_NOTIFICATION_EFFECT = "telegram_direct_notification_effect"
EXTERNAL_EFFECT_SCOPE_TELEGRAM_OTP_DELIVERY = "telegram_otp_delivery"
EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY = "sms_provider_delivery"
EXTERNAL_EFFECT_SCOPE_WEB_PUSH_DELIVERY = "web_push_delivery"

EXTERNAL_EFFECT_EXECUTION_SCOPES = frozenset(
    {
        EXTERNAL_EFFECT_SCOPE_TRADE_WEBAPP_DELIVERY,
        EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY,
        EXTERNAL_EFFECT_SCOPE_TELEGRAM_ADMIN_BROADCAST_DELIVERY,
        EXTERNAL_EFFECT_SCOPE_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY,
        EXTERNAL_EFFECT_SCOPE_OFFER_TELEGRAM_PUBLICATION,
        EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_RUNTIME,
        EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT,
        EXTERNAL_EFFECT_SCOPE_TELEGRAM_DIRECT_NOTIFICATION_EFFECT,
        EXTERNAL_EFFECT_SCOPE_TELEGRAM_OTP_DELIVERY,
        EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY,
        EXTERNAL_EFFECT_SCOPE_WEB_PUSH_DELIVERY,
    }
)

EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_FIELDS = frozenset(
    {
        "schema",
        "authorization_id",
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "writer_term_issued_at",
        "writer_term_expires_at",
        "witness_transition_id",
        "authorized_scopes",
        "reconciliation_decision",
        "reconciliation_evidence_sha256",
        "reconciliation_completed_at",
        "issued_at",
        "expires_at",
    }
)

DEFAULT_EXTERNAL_EFFECT_AUTHORIZATION_SAFETY_MARGIN_SECONDS = 5
DEFAULT_EXTERNAL_EFFECT_AUTHORIZATION_MAX_DURATION_SECONDS = 60
MIN_EXTERNAL_EFFECT_AUTHORIZATION_SAFETY_MARGIN_SECONDS = 1
MAX_EXTERNAL_EFFECT_AUTHORIZATION_SAFETY_MARGIN_SECONDS = 60
MIN_EXTERNAL_EFFECT_AUTHORIZATION_MAX_DURATION_SECONDS = 2
MAX_EXTERNAL_EFFECT_AUTHORIZATION_MAX_DURATION_SECONDS = 300

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ExternalEffectExecutionGateError(RuntimeError):
    """Raised when an enabled effectful-worker authorization is not safe."""


@dataclass(frozen=True)
class ExternalEffectExecutionGatePolicy:
    """Explicit local policy for a term-scoped external-effect authorization.

    Production settings always force ``owner_uid`` to ``0``.  A different
    owner is accepted only by direct construction so isolated non-root tests
    can exercise the exact file checks without weakening deployed settings.
    """

    enabled: bool = False
    local_site: str | None = None
    authorization_file: Path | None = None
    owner_uid: int = 0
    safety_margin_seconds: int = DEFAULT_EXTERNAL_EFFECT_AUTHORIZATION_SAFETY_MARGIN_SECONDS
    max_authorization_duration_seconds: int = DEFAULT_EXTERNAL_EFFECT_AUTHORIZATION_MAX_DURATION_SECONDS


@dataclass(frozen=True)
class ExternalEffectExecutionAuthorization:
    """One non-secret local authorization written by a root-side controller."""

    authorization_id: str
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    writer_term_issued_at: datetime
    writer_term_expires_at: datetime
    witness_transition_id: str
    authorized_scopes: tuple[str, ...]
    reconciliation_decision: str
    reconciliation_evidence_sha256: str
    reconciliation_completed_at: datetime
    issued_at: datetime
    expires_at: datetime


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ExternalEffectExecutionGateError("external-effect authorization clock must be timezone-aware")
    return now.astimezone(timezone.utc)


def _utc_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ExternalEffectExecutionGateError(f"external-effect authorization {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExternalEffectExecutionGateError(
            f"external-effect authorization {label} is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise ExternalEffectExecutionGateError(f"external-effect authorization {label} is invalid")
    return parsed.astimezone(timezone.utc)


def _render_timestamp(value: datetime, *, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ExternalEffectExecutionGateError(f"external-effect authorization {label} is invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 128:
        raise ExternalEffectExecutionGateError(f"external-effect authorization {label} is invalid")
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExternalEffectExecutionGateError(
                "external-effect authorization contains duplicate JSON fields"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ExternalEffectExecutionGateError(
        f"external-effect authorization JSON constant is forbidden: {value}"
    )


def _parse_scopes(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ExternalEffectExecutionGateError("external-effect authorization scopes are invalid")
    if any(not isinstance(item, str) for item in value):
        raise ExternalEffectExecutionGateError("external-effect authorization scopes are invalid")
    scopes = tuple(value)
    if (
        len(set(scopes)) != len(scopes)
        or tuple(sorted(scopes)) != scopes
        or any(item not in EXTERNAL_EFFECT_EXECUTION_SCOPES for item in scopes)
    ):
        raise ExternalEffectExecutionGateError("external-effect authorization scopes are invalid")
    return scopes


def parse_external_effect_execution_authorization(
    value: object,
) -> ExternalEffectExecutionAuthorization:
    """Parse an exact authorization schema without touching the filesystem."""

    if not isinstance(value, Mapping) or set(value) != EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_FIELDS:
        raise ExternalEffectExecutionGateError("external-effect authorization fields are invalid")
    if value.get("schema") != EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_SCHEMA:
        raise ExternalEffectExecutionGateError("external-effect authorization schema is invalid")

    holder_site = value.get("holder_site")
    if holder_site not in WEBAPP_SITES:
        raise ExternalEffectExecutionGateError("external-effect authorization holder site is invalid")
    writer_epoch = value.get("writer_epoch")
    if type(writer_epoch) is not int or writer_epoch < 1:
        raise ExternalEffectExecutionGateError("external-effect authorization writer epoch is invalid")
    evidence_hash = value.get("reconciliation_evidence_sha256")
    if not isinstance(evidence_hash, str) or _SHA256_RE.fullmatch(evidence_hash.lower()) is None:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization reconciliation evidence hash is invalid"
        )
    decision = value.get("reconciliation_decision")
    if decision != RECONCILIATION_DECISION_COMPLETE_NO_RESEND:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization reconciliation decision is invalid"
        )

    authorization = ExternalEffectExecutionAuthorization(
        authorization_id=_bounded_identifier(value.get("authorization_id"), label="ID"),
        holder_site=holder_site,
        writer_epoch=writer_epoch,
        writer_lease_id=_bounded_identifier(value.get("writer_lease_id"), label="writer lease ID"),
        writer_term_issued_at=_utc_timestamp(value.get("writer_term_issued_at"), label="term issued time"),
        writer_term_expires_at=_utc_timestamp(value.get("writer_term_expires_at"), label="term expiry"),
        witness_transition_id=_bounded_identifier(
            value.get("witness_transition_id"), label="Witness transition ID"
        ),
        authorized_scopes=_parse_scopes(value.get("authorized_scopes")),
        reconciliation_decision=decision,
        reconciliation_evidence_sha256=evidence_hash.lower(),
        reconciliation_completed_at=_utc_timestamp(
            value.get("reconciliation_completed_at"), label="reconciliation completion time"
        ),
        issued_at=_utc_timestamp(value.get("issued_at"), label="issued time"),
        expires_at=_utc_timestamp(value.get("expires_at"), label="expiry"),
    )
    if authorization.writer_term_expires_at <= authorization.writer_term_issued_at:
        raise ExternalEffectExecutionGateError("external-effect authorization term interval is invalid")
    if authorization.expires_at <= authorization.issued_at:
        raise ExternalEffectExecutionGateError("external-effect authorization interval is invalid")
    if (
        authorization.reconciliation_completed_at < authorization.writer_term_issued_at
        or authorization.reconciliation_completed_at > authorization.issued_at
    ):
        raise ExternalEffectExecutionGateError(
            "external-effect authorization reconciliation time is outside its term"
        )
    if authorization.issued_at < authorization.writer_term_issued_at:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization was issued before its writer term"
        )
    if authorization.expires_at > authorization.writer_term_expires_at:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization outlives its writer term"
        )
    return authorization


def external_effect_execution_authorization_mapping(
    authorization: ExternalEffectExecutionAuthorization,
) -> dict[str, object]:
    """Return canonical non-secret JSON fields for an explicit installer.

    The function validates the dataclass through the same strict parser used
    by readers.  It does not inspect a live term, filesystem, or network.
    """

    if not isinstance(authorization, ExternalEffectExecutionAuthorization):
        raise ExternalEffectExecutionGateError("external-effect authorization is invalid")
    value: dict[str, object] = {
        "schema": EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_SCHEMA,
        "authorization_id": authorization.authorization_id,
        "holder_site": authorization.holder_site,
        "writer_epoch": authorization.writer_epoch,
        "writer_lease_id": authorization.writer_lease_id,
        "writer_term_issued_at": _render_timestamp(
            authorization.writer_term_issued_at, label="term issued time"
        ),
        "writer_term_expires_at": _render_timestamp(
            authorization.writer_term_expires_at, label="term expiry"
        ),
        "witness_transition_id": authorization.witness_transition_id,
        "authorized_scopes": list(authorization.authorized_scopes),
        "reconciliation_decision": authorization.reconciliation_decision,
        "reconciliation_evidence_sha256": authorization.reconciliation_evidence_sha256,
        "reconciliation_completed_at": _render_timestamp(
            authorization.reconciliation_completed_at,
            label="reconciliation completion time",
        ),
        "issued_at": _render_timestamp(authorization.issued_at, label="issued time"),
        "expires_at": _render_timestamp(authorization.expires_at, label="expiry"),
    }
    # Keep writer and reader validation identical even for an in-memory caller.
    parse_external_effect_execution_authorization(value)
    return value


def _validate_controlled_directory(info: os.stat_result, *, owner_uid: int) -> None:
    if not stat.S_ISDIR(info.st_mode):
        raise ExternalEffectExecutionGateError("external-effect authorization ancestor is not a directory")
    if info.st_uid not in {0, owner_uid}:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization ancestor is not owner controlled"
        )
    mode = stat.S_IMODE(info.st_mode)
    sticky_root_parent = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if mode & 0o022 and not sticky_root_parent:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization ancestor is writable by an unsafe principal"
        )


def _validate_authorization_ancestors(path: Path, *, owner_uid: int) -> None:
    """Validate every parent via descriptors before opening the leaf.

    A root-owned non-writable directory is accepted as an ancestor of a test
    owner directory, which permits safe fixtures below ``/tmp`` without
    weakening the production root-only leaf requirement.
    """

    if not isinstance(path, Path) or not path.is_absolute():
        raise ExternalEffectExecutionGateError("external-effect authorization path must be absolute")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization ancestor validation is unavailable"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow | directory
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        _validate_controlled_directory(os.fstat(descriptor), owner_uid=owner_uid)
        for component in path.parts[1:-1]:
            if component in {"", ".", ".."}:
                raise ExternalEffectExecutionGateError(
                    "external-effect authorization path is not canonical"
                )
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            _validate_controlled_directory(os.fstat(descriptor), owner_uid=owner_uid)
    except ExternalEffectExecutionGateError:
        raise
    except OSError as exc:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization ancestors are unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_root_owned_authorization_bytes(path: Path, *, owner_uid: int) -> bytes:
    _validate_authorization_ancestors(path, owner_uid=owner_uid)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization symlink protection is unavailable"
        )
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > MAX_EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_BYTES
        ):
            raise ExternalEffectExecutionGateError(
                "external-effect authorization is not a root-owned 0600 regular file"
            )
        chunks: list[bytes] = []
        remaining = MAX_EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise ExternalEffectExecutionGateError(
                "external-effect authorization changed while being read"
            )
    except ExternalEffectExecutionGateError:
        raise
    except OSError as exc:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization cannot be opened safely"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not payload or len(payload) > MAX_EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_BYTES:
        raise ExternalEffectExecutionGateError("external-effect authorization size is invalid")
    return payload


def load_external_effect_execution_authorization(
    path: Path,
    *,
    owner_uid: int = 0,
) -> ExternalEffectExecutionAuthorization:
    """Load one stable root-owned authorization without provider I/O."""

    if type(owner_uid) is not int or owner_uid < 0:
        raise ExternalEffectExecutionGateError("external-effect authorization owner UID is invalid")
    raw = _read_root_owned_authorization_bytes(path, owner_uid=owner_uid)
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ExternalEffectExecutionGateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalEffectExecutionGateError("external-effect authorization JSON is invalid") from exc
    return parse_external_effect_execution_authorization(value)


def _enabled_policy_values(
    policy: ExternalEffectExecutionGatePolicy,
) -> tuple[str, Path, int, int, int]:
    if not isinstance(policy.local_site, str) or policy.local_site not in WEBAPP_SITES:
        raise ExternalEffectExecutionGateError("external-effect authorization local site is invalid")
    if not isinstance(policy.authorization_file, Path):
        raise ExternalEffectExecutionGateError("external-effect authorization file is required")
    if not policy.authorization_file.is_absolute():
        raise ExternalEffectExecutionGateError("external-effect authorization file must be absolute")
    if type(policy.owner_uid) is not int or policy.owner_uid < 0:
        raise ExternalEffectExecutionGateError("external-effect authorization owner UID is invalid")
    if (
        type(policy.safety_margin_seconds) is not int
        or not MIN_EXTERNAL_EFFECT_AUTHORIZATION_SAFETY_MARGIN_SECONDS
        <= policy.safety_margin_seconds
        <= MAX_EXTERNAL_EFFECT_AUTHORIZATION_SAFETY_MARGIN_SECONDS
    ):
        raise ExternalEffectExecutionGateError("external-effect authorization safety margin is invalid")
    if (
        type(policy.max_authorization_duration_seconds) is not int
        or not MIN_EXTERNAL_EFFECT_AUTHORIZATION_MAX_DURATION_SECONDS
        <= policy.max_authorization_duration_seconds
        <= MAX_EXTERNAL_EFFECT_AUTHORIZATION_MAX_DURATION_SECONDS
        or policy.max_authorization_duration_seconds <= policy.safety_margin_seconds
    ):
        raise ExternalEffectExecutionGateError("external-effect authorization maximum duration is invalid")
    return (
        policy.local_site,
        policy.authorization_file,
        policy.owner_uid,
        policy.safety_margin_seconds,
        policy.max_authorization_duration_seconds,
    )


def policy_from_settings(settings: object) -> ExternalEffectExecutionGatePolicy:
    """Project application settings without opening a file on the disabled path."""

    enabled = getattr(settings, "external_effect_execution_gate_enforced", False)
    if enabled is False:
        return ExternalEffectExecutionGatePolicy()
    if enabled is not True:
        raise ExternalEffectExecutionGateError("external-effect authorization enabled flag is invalid")

    raw_path = getattr(settings, "external_effect_execution_gate_authorization_file", None)
    if raw_path is None or raw_path == "":
        authorization_file = None
    elif isinstance(raw_path, Path):
        authorization_file = raw_path
    elif isinstance(raw_path, str):
        authorization_file = Path(raw_path)
    else:
        raise ExternalEffectExecutionGateError("external-effect authorization file setting is invalid")
    return ExternalEffectExecutionGatePolicy(
        enabled=True,
        local_site=getattr(settings, "external_effect_execution_gate_local_site", None),
        authorization_file=authorization_file,
        # Runtime settings cannot weaken the root-owned production boundary.
        owner_uid=0,
        safety_margin_seconds=getattr(
            settings,
            "external_effect_execution_gate_safety_margin_seconds",
            DEFAULT_EXTERNAL_EFFECT_AUTHORIZATION_SAFETY_MARGIN_SECONDS,
        ),
        max_authorization_duration_seconds=getattr(
            settings,
            "external_effect_execution_gate_max_authorization_duration_seconds",
            DEFAULT_EXTERNAL_EFFECT_AUTHORIZATION_MAX_DURATION_SECONDS,
        ),
    )


def _term_fields_match(
    authorization: ExternalEffectExecutionAuthorization,
    term: ValidatedWriterTerm,
) -> bool:
    return (
        authorization.holder_site == term.holder_site
        and authorization.writer_epoch == term.writer_epoch
        and authorization.writer_lease_id == term.lease_id
        and authorization.writer_term_issued_at == term.issued_at
        and authorization.writer_term_expires_at == term.expires_at
        and authorization.witness_transition_id == term.witness_transition_id
    )


def same_validated_writer_term(
    first: ValidatedWriterTerm | None,
    second: ValidatedWriterTerm | None,
) -> bool:
    """Return whether two non-secret terms name the exact same Witness term."""

    return (
        isinstance(first, ValidatedWriterTerm)
        and isinstance(second, ValidatedWriterTerm)
        and first.holder_site == second.holder_site
        and first.writer_epoch == second.writer_epoch
        and first.lease_id == second.lease_id
        and first.issued_at == second.issued_at
        and first.expires_at == second.expires_at
        and first.witness_transition_id == second.witness_transition_id
    )


def require_external_effect_execution_authorization(
    policy: ExternalEffectExecutionGatePolicy,
    *,
    active_writer_term: ValidatedWriterTerm | None,
    scope: str,
    now: datetime | None = None,
) -> ExternalEffectExecutionAuthorization | None:
    """Permit one scope only when its active term-bound authorization is fresh.

    A disabled policy returns ``None`` without reading the term or the file.
    An enabled policy requires the caller to have just validated the active
    Writer Witness term.  The runtime wrapper revalidates that term once more
    after this function returns, closing the ordinary file-replacement race at
    the worker boundary.
    """

    if not isinstance(policy, ExternalEffectExecutionGatePolicy):
        raise ExternalEffectExecutionGateError("external-effect authorization policy is invalid")
    if policy.enabled is False:
        return None
    if policy.enabled is not True:
        raise ExternalEffectExecutionGateError("external-effect authorization enabled flag is invalid")
    if scope not in EXTERNAL_EFFECT_EXECUTION_SCOPES:
        raise ExternalEffectExecutionGateError("external-effect authorization scope is invalid")
    (
        local_site,
        authorization_file,
        owner_uid,
        safety_margin_seconds,
        maximum_duration_seconds,
    ) = _enabled_policy_values(policy)
    if not isinstance(active_writer_term, ValidatedWriterTerm):
        raise ExternalEffectExecutionGateError(
            "external-effect authorization requires an enabled active Writer Witness term"
        )
    observed_at = _utc_now(now)
    if active_writer_term.issued_at > observed_at:
        raise ExternalEffectExecutionGateError("active Writer Witness term is not active yet")
    if active_writer_term.expires_at <= observed_at:
        raise ExternalEffectExecutionGateError("active Writer Witness term is expired")
    authorization = load_external_effect_execution_authorization(
        authorization_file,
        owner_uid=owner_uid,
    )
    if active_writer_term.holder_site != local_site:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization active holder does not match the local site"
        )
    if authorization.holder_site != local_site:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization holder does not match the local site"
        )
    if not _term_fields_match(authorization, active_writer_term):
        raise ExternalEffectExecutionGateError(
            "external-effect authorization does not bind the active Writer Witness term"
        )
    if authorization.reconciliation_decision != RECONCILIATION_DECISION_COMPLETE_NO_RESEND:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization reconciliation decision is not no-resend"
        )
    if scope not in authorization.authorized_scopes:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization does not permit this worker scope"
        )
    if authorization.issued_at > observed_at:
        raise ExternalEffectExecutionGateError("external-effect authorization is not active yet")
    if authorization.expires_at <= observed_at:
        raise ExternalEffectExecutionGateError("external-effect authorization is expired")
    if authorization.expires_at - authorization.issued_at > timedelta(
        seconds=maximum_duration_seconds
    ):
        raise ExternalEffectExecutionGateError(
            "external-effect authorization duration exceeds the configured maximum"
        )
    if observed_at - authorization.issued_at > timedelta(seconds=maximum_duration_seconds):
        raise ExternalEffectExecutionGateError("external-effect authorization is stale")
    safety_margin = timedelta(seconds=safety_margin_seconds)
    if authorization.expires_at <= observed_at + safety_margin:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization expires within the required safety margin"
        )
    if active_writer_term.expires_at <= observed_at + safety_margin:
        raise ExternalEffectExecutionGateError(
            "active Writer Witness term expires within the required safety margin"
        )
    return authorization


def _existing_target_is_safe_for_replace(path: Path, *, owner_uid: int) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization target cannot be inspected safely"
        ) from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != owner_uid
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise ExternalEffectExecutionGateError(
            "external-effect authorization target is not a root-owned 0600 regular file"
        )


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short external-effect authorization write")
        offset += written


def write_external_effect_execution_authorization(
    path: Path,
    authorization: ExternalEffectExecutionAuthorization,
    *,
    owner_uid: int = 0,
) -> None:
    """Explicitly atomically install one root-owned authorization receipt.

    This is intentionally not called by application or worker startup.  A
    root-side controller/operator must first reconcile provider state and make
    the no-resend decision, then pass that evidence-bound decision here.  The
    function writes a fresh ``0600`` temporary file, fsyncs it, atomically
    replaces the leaf, and fsyncs the controlled parent directory.
    """

    if not isinstance(path, Path) or not path.is_absolute():
        raise ExternalEffectExecutionGateError("external-effect authorization path must be absolute")
    if type(owner_uid) is not int or owner_uid < 0:
        raise ExternalEffectExecutionGateError("external-effect authorization owner UID is invalid")
    if os.geteuid() != owner_uid:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization writer is not the required owner"
        )
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ExternalEffectExecutionGateError(
            "external-effect authorization atomic writer is unavailable on this platform"
        )
    _validate_authorization_ancestors(path, owner_uid=owner_uid)
    _existing_target_is_safe_for_replace(path, owner_uid=owner_uid)
    payload = json.dumps(
        external_effect_execution_authorization_mapping(authorization),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii") + b"\n"
    if len(payload) > MAX_EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_BYTES:
        raise ExternalEffectExecutionGateError("external-effect authorization size is invalid")

    temporary = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
    temporary_created = False
    descriptor = -1
    directory_descriptor = -1
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW
        )
        descriptor = os.open(temporary, flags, 0o600)
        temporary_created = True
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size != len(payload)
        ):
            raise ExternalEffectExecutionGateError(
                "external-effect authorization temporary file is unsafe"
            )
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        temporary_created = False
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
        )
        directory_descriptor = os.open(path.parent, directory_flags)
        _validate_controlled_directory(os.fstat(directory_descriptor), owner_uid=owner_uid)
        os.fsync(directory_descriptor)
    except ExternalEffectExecutionGateError:
        raise
    except OSError as exc:
        raise ExternalEffectExecutionGateError(
            "external-effect authorization cannot be atomically written"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if temporary_created:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                # The controlled root-only parent prevents an untrusted
                # replacement; retain an interrupted temporary for inspection
                # rather than masking the original write failure.
                pass


__all__ = [
    "DEFAULT_EXTERNAL_EFFECT_AUTHORIZATION_MAX_DURATION_SECONDS",
    "DEFAULT_EXTERNAL_EFFECT_AUTHORIZATION_SAFETY_MARGIN_SECONDS",
    "EXTERNAL_EFFECT_EXECUTION_AUTHORIZATION_SCHEMA",
    "EXTERNAL_EFFECT_EXECUTION_SCOPES",
    "EXTERNAL_EFFECT_SCOPE_OFFER_TELEGRAM_PUBLICATION",
    "EXTERNAL_EFFECT_SCOPE_TELEGRAM_ADMIN_BROADCAST_DELIVERY",
    "EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_API_EFFECT",
    "EXTERNAL_EFFECT_SCOPE_TELEGRAM_BOT_RUNTIME",
    "EXTERNAL_EFFECT_SCOPE_TELEGRAM_DIRECT_NOTIFICATION_EFFECT",
    "EXTERNAL_EFFECT_SCOPE_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY",
    "EXTERNAL_EFFECT_SCOPE_TELEGRAM_OTP_DELIVERY",
    "EXTERNAL_EFFECT_SCOPE_SMS_PROVIDER_DELIVERY",
    "EXTERNAL_EFFECT_SCOPE_TRADE_TELEGRAM_DELIVERY",
    "EXTERNAL_EFFECT_SCOPE_TRADE_WEBAPP_DELIVERY",
    "EXTERNAL_EFFECT_SCOPE_WEB_PUSH_DELIVERY",
    "ExternalEffectExecutionAuthorization",
    "ExternalEffectExecutionGateError",
    "ExternalEffectExecutionGatePolicy",
    "RECONCILIATION_DECISION_COMPLETE_NO_RESEND",
    "external_effect_execution_authorization_mapping",
    "load_external_effect_execution_authorization",
    "parse_external_effect_execution_authorization",
    "policy_from_settings",
    "require_external_effect_execution_authorization",
    "same_validated_writer_term",
    "write_external_effect_execution_authorization",
]
