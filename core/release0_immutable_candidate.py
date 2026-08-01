"""Pure, non-authorizing identity contract for a future Release-0 candidate.

The historical ``2c08`` release remains hard-blocked by the existing FI and
WA-IR admission paths.  This module deliberately does not alter those paths.
It defines the signed evidence a *new* immutable candidate must present before
later, separately reviewed term-bound Compose and host preflight work can use
it.

There is no filesystem, Git, Docker, network, Object Storage, DNS, service
manager, Writer Witness, or writer-authority operation here.  Verification of
this document is evidence only; it cannot grant any operational capability.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Final, Mapping
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


__all__ = (
    "CANDIDATE_CRITICAL_SOURCE_FILES",
    "LEGACY_APPLICATION_RELEASE_SHA",
    "RELEASE0_IMMUTABLE_CANDIDATE_SCHEMA",
    "RELEASE0_TERM_CONTRACT_SCHEMA",
    "Release0CandidateAuthority",
    "Release0ImmutableCandidate",
    "Release0ImmutableCandidateError",
    "Release0TermContract",
    "canonical_release0_immutable_candidate_json_bytes",
    "require_verified_release0_immutable_candidate",
    "verify_release0_immutable_candidate",
)


# This is an explicit deny-list entry, not a mutable default.  A future
# candidate must be a new source identity with the early Writer Witness code.
LEGACY_APPLICATION_RELEASE_SHA: Final = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
RELEASE0_IMMUTABLE_CANDIDATE_SCHEMA: Final = (
    "gold-trade-release0-immutable-candidate-v1"
)
RELEASE0_TERM_CONTRACT_SCHEMA: Final = "gold-trade-release0-term-contract-v1"
_SIGNING_DOMAIN: Final = b"gold-trade-release0-immutable-candidate-v1\x00"

# These are the source files whose exact bytes establish the term-bound
# process-start and egress boundary.  The list is deliberately closed: adding
# a similar-looking file cannot silently expand a candidate's claim.
CANDIDATE_CRITICAL_SOURCE_FILES: Final = (
    "core/application_writer_term.py",
    "core/db.py",
    "core/sms.py",
    "core/telegram_gateway.py",
    "core/web_push.py",
    "main.py",
    "run_bot.py",
    "bot/middlewares/writer_term.py",
    "bot/writer_readiness.py",
)

# These files intentionally do not exist in the historical release.  A later
# Compose slice has to create both and bind their exact bytes before this
# contract can verify a candidate on a local release root.
_FI_WRITER_COMPOSE_PATH: Final = (
    "deploy/production/docker-compose.webapp-fi-writer-release0.yml"
)
_IR_PROMOTED_COMPOSE_PATH: Final = (
    "deploy/production/docker-compose.webapp-ir-promoted-release0.yml"
)

_SHA40_RE: Final = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IMAGE_ID_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_IMAGE_DIGEST_RE: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,511}@sha256:[0-9a-f]{64}$", re.ASCII
)
_KEY_ID_RE: Final = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_PATH_RE: Final = re.compile(r"^/[A-Za-z0-9._/-]+$", re.ASCII)
_CANDIDATE_ID_RE: Final = re.compile(
    r"^release0-[0-9a-f]{12}-[0-9a-f]{12}$", re.ASCII
)

_DOCUMENT_FIELDS: Final = frozenset(
    {
        "schema",
        "candidate_id",
        "application",
        "control",
        "images",
        "term_contract",
        "critical_source_files",
        "compose",
        "signer_key_id",
        "signature_base64",
    }
)
_RELEASE_FIELDS: Final = frozenset({"release_sha", "tree_sha", "release_root"})
_SERVICE_FIELDS: Final = frozenset({"image_repo_digest", "image_id"})
_TERM_FIELDS: Final = frozenset(
    {
        "schema",
        "single_writer_runtime_enabled",
        "application_writer_term_enforced",
        "database_schema_bootstrap_enabled",
        "api_background_jobs_enabled",
        "lease_duration_seconds",
        "safety_margin_seconds",
        "renew_interval_seconds",
    }
)
_COMPOSE_FIELDS: Final = frozenset(
    {
        "fi_writer_relative_path",
        "fi_writer_sha256",
        "ir_promoted_relative_path",
        "ir_promoted_sha256",
    }
)
_VERIFICATION_CAPABILITY: Final = object()
_VERIFIED_STATES: WeakKeyDictionary["Release0ImmutableCandidate", tuple[object, ...]] = (
    WeakKeyDictionary()
)


class Release0ImmutableCandidateError(ValueError):
    """The candidate descriptor cannot prove a safe immutable identity."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise Release0ImmutableCandidateError(code)


def canonical_release0_immutable_candidate_json_bytes(value: object) -> bytes:
    """Return the one canonical ASCII encoding accepted by this contract."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise Release0ImmutableCandidateError(
            "RELEASE0_CANDIDATE_DOCUMENT_INVALID"
        ) from exc


@dataclass(frozen=True)
class Release0CandidateAuthority:
    """Pinned Ed25519 public authority supplied by a root-owned caller."""

    public_key: bytes = b""
    key_id: str = ""


@dataclass(frozen=True)
class _ReleaseBinding:
    release_sha: str
    tree_sha: str
    release_root: str


@dataclass(frozen=True)
class _ImageBinding:
    image_repo_digest: str
    image_id: str


@dataclass(frozen=True)
class Release0TermContract:
    """Closed configuration claim needed by a future term-bound Compose pair."""

    lease_duration_seconds: int
    safety_margin_seconds: int
    renew_interval_seconds: int


@dataclass(frozen=True, eq=False)
class Release0ImmutableCandidate:
    """A verified candidate identity that is deliberately non-authorizing."""

    candidate_id: str
    application: _ReleaseBinding
    control: _ReleaseBinding
    app_image: _ImageBinding
    bot_image: _ImageBinding
    term_contract: Release0TermContract
    critical_source_files: tuple[tuple[str, str], ...]
    fi_writer_compose_sha256: str
    ir_promoted_compose_sha256: str
    signer_key_id: str
    identity_sha256: str
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _verification_capability: object | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("RELEASE0_CANDIDATE_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("RELEASE0_CANDIDATE_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("RELEASE0_CANDIDATE_COPY_FORBIDDEN")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("RELEASE0_CANDIDATE_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    _fail("RELEASE0_CANDIDATE_JSON_CONSTANT_FORBIDDEN")


def _sha40(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA40_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _absolute_release_root(value: object, *, release_sha: str, code: str) -> str:
    if type(value) is not str or _PATH_RE.fullmatch(value) is None:
        _fail(code)
    if ".." in value.split("/") or value.rstrip("/").rsplit("/", 1)[-1] != release_sha:
        _fail(code)
    return value


def _authority(value: object) -> Release0CandidateAuthority:
    if type(value) is not Release0CandidateAuthority:
        _fail("RELEASE0_CANDIDATE_AUTHORITY_INVALID")
    if type(value.public_key) is not bytes or len(value.public_key) != 32:
        _fail("RELEASE0_CANDIDATE_AUTHORITY_INVALID")
    if type(value.key_id) is not str or _KEY_ID_RE.fullmatch(value.key_id) is None:
        _fail("RELEASE0_CANDIDATE_AUTHORITY_INVALID")
    if hashlib.sha256(value.public_key).hexdigest() != value.key_id.removeprefix(
        "ed25519-sha256:"
    ):
        _fail("RELEASE0_CANDIDATE_AUTHORITY_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(value.public_key)
    except ValueError:
        _fail("RELEASE0_CANDIDATE_AUTHORITY_INVALID")
    return value


def _release(value: object, *, label: str) -> _ReleaseBinding:
    if not isinstance(value, Mapping) or set(value) != _RELEASE_FIELDS:
        _fail(f"RELEASE0_CANDIDATE_{label}_RELEASE_INVALID")
    release_sha = _sha40(
        value.get("release_sha"), code=f"RELEASE0_CANDIDATE_{label}_RELEASE_INVALID"
    )
    if release_sha == LEGACY_APPLICATION_RELEASE_SHA:
        _fail("RELEASE0_CANDIDATE_LEGACY_2C08_REJECTED")
    tree_sha = _sha40(
        value.get("tree_sha"), code=f"RELEASE0_CANDIDATE_{label}_RELEASE_INVALID"
    )
    root = _absolute_release_root(
        value.get("release_root"),
        release_sha=release_sha,
        code=f"RELEASE0_CANDIDATE_{label}_ROOT_INVALID",
    )
    return _ReleaseBinding(release_sha=release_sha, tree_sha=tree_sha, release_root=root)


def _image(value: object) -> _ImageBinding:
    if not isinstance(value, Mapping) or set(value) != _SERVICE_FIELDS:
        _fail("RELEASE0_CANDIDATE_IMAGE_INVALID")
    repo_digest = value.get("image_repo_digest")
    image_id = value.get("image_id")
    if type(repo_digest) is not str or _IMAGE_DIGEST_RE.fullmatch(repo_digest) is None:
        _fail("RELEASE0_CANDIDATE_IMAGE_INVALID")
    if type(image_id) is not str or _IMAGE_ID_RE.fullmatch(image_id) is None:
        _fail("RELEASE0_CANDIDATE_IMAGE_INVALID")
    return _ImageBinding(image_repo_digest=repo_digest, image_id=image_id)


def _term_contract(value: object) -> Release0TermContract:
    if not isinstance(value, Mapping) or set(value) != _TERM_FIELDS:
        _fail("RELEASE0_CANDIDATE_TERM_CONTRACT_INVALID")
    if value.get("schema") != RELEASE0_TERM_CONTRACT_SCHEMA:
        _fail("RELEASE0_CANDIDATE_TERM_CONTRACT_INVALID")
    expected_boolean_values = {
        "single_writer_runtime_enabled": True,
        "application_writer_term_enforced": True,
        "database_schema_bootstrap_enabled": False,
        "api_background_jobs_enabled": False,
    }
    if any(value.get(field) is not expected for field, expected in expected_boolean_values.items()):
        _fail("RELEASE0_CANDIDATE_TERM_CONTRACT_INVALID")
    duration = value.get("lease_duration_seconds")
    margin = value.get("safety_margin_seconds")
    renew = value.get("renew_interval_seconds")
    if (
        type(duration) is not int
        or type(margin) is not int
        or type(renew) is not int
        or (duration, margin, renew) != (60, 15, 10)
    ):
        _fail("RELEASE0_CANDIDATE_TERM_CONTRACT_INVALID")
    return Release0TermContract(
        lease_duration_seconds=duration,
        safety_margin_seconds=margin,
        renew_interval_seconds=renew,
    )


def _critical_source_files(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping) or set(value) != set(CANDIDATE_CRITICAL_SOURCE_FILES):
        _fail("RELEASE0_CANDIDATE_CRITICAL_SOURCE_FILES_INVALID")
    return tuple(
        (
            path,
            _sha256(
                value.get(path),
                code="RELEASE0_CANDIDATE_CRITICAL_SOURCE_FILES_INVALID",
            ),
        )
        for path in CANDIDATE_CRITICAL_SOURCE_FILES
    )


def _compose(value: object) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != _COMPOSE_FIELDS:
        _fail("RELEASE0_CANDIDATE_COMPOSE_INVALID")
    if (
        value.get("fi_writer_relative_path") != _FI_WRITER_COMPOSE_PATH
        or value.get("ir_promoted_relative_path") != _IR_PROMOTED_COMPOSE_PATH
    ):
        _fail("RELEASE0_CANDIDATE_COMPOSE_INVALID")
    return (
        _sha256(
            value.get("fi_writer_sha256"), code="RELEASE0_CANDIDATE_COMPOSE_INVALID"
        ),
        _sha256(
            value.get("ir_promoted_sha256"), code="RELEASE0_CANDIDATE_COMPOSE_INVALID"
        ),
    )


def _candidate_state(value: Release0ImmutableCandidate) -> tuple[object, ...]:
    return (
        value.candidate_id,
        value.application.release_sha,
        value.application.tree_sha,
        value.application.release_root,
        value.control.release_sha,
        value.control.tree_sha,
        value.control.release_root,
        value.app_image.image_repo_digest,
        value.app_image.image_id,
        value.bot_image.image_repo_digest,
        value.bot_image.image_id,
        value.term_contract.lease_duration_seconds,
        value.term_contract.safety_margin_seconds,
        value.term_contract.renew_interval_seconds,
        value.critical_source_files,
        value.fi_writer_compose_sha256,
        value.ir_promoted_compose_sha256,
        value.signer_key_id,
        value.identity_sha256,
    )


def require_verified_release0_immutable_candidate(
    value: object,
) -> Release0ImmutableCandidate:
    """Accept only a descriptor minted by this verifier in this process."""

    if (
        type(value) is not Release0ImmutableCandidate
        or value._verification_capability is not _VERIFICATION_CAPABILITY
        or value not in _VERIFIED_STATES
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
        or _VERIFIED_STATES[value] != _candidate_state(value)
    ):
        _fail("RELEASE0_CANDIDATE_UNVERIFIED")
    return value


def verify_release0_immutable_candidate(
    document: bytes,
    *,
    authority: Release0CandidateAuthority,
) -> Release0ImmutableCandidate:
    """Verify one closed, signed candidate descriptor without side effects."""

    pinned = _authority(authority)
    if type(document) is not bytes or not document or len(document) > 64 * 1024:
        _fail("RELEASE0_CANDIDATE_DOCUMENT_INVALID")
    try:
        parsed = json.loads(
            document.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except Release0ImmutableCandidateError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError, RecursionError):
        _fail("RELEASE0_CANDIDATE_DOCUMENT_INVALID")
    if not isinstance(parsed, dict) or set(parsed) != _DOCUMENT_FIELDS:
        _fail("RELEASE0_CANDIDATE_FIELDS_INVALID")
    try:
        canonical_document = canonical_release0_immutable_candidate_json_bytes(parsed)
    except Release0ImmutableCandidateError:
        _fail("RELEASE0_CANDIDATE_DOCUMENT_INVALID")
    if canonical_document != document:
        _fail("RELEASE0_CANDIDATE_CANONICAL_REQUIRED")
    if parsed.get("schema") != RELEASE0_IMMUTABLE_CANDIDATE_SCHEMA:
        _fail("RELEASE0_CANDIDATE_SCHEMA_INVALID")

    application = _release(parsed.get("application"), label="APPLICATION")
    control = _release(parsed.get("control"), label="CONTROL")
    candidate_id = parsed.get("candidate_id")
    expected_candidate_id = (
        f"release0-{application.release_sha[:12]}-{control.release_sha[:12]}"
    )
    if (
        type(candidate_id) is not str
        or _CANDIDATE_ID_RE.fullmatch(candidate_id) is None
        or candidate_id != expected_candidate_id
    ):
        _fail("RELEASE0_CANDIDATE_ID_INVALID")

    images = parsed.get("images")
    if not isinstance(images, Mapping) or set(images) != {"app", "bot"}:
        _fail("RELEASE0_CANDIDATE_IMAGE_INVALID")
    app_image = _image(images.get("app"))
    bot_image = _image(images.get("bot"))
    term_contract = _term_contract(parsed.get("term_contract"))
    critical_files = _critical_source_files(parsed.get("critical_source_files"))
    fi_compose_sha, ir_compose_sha = _compose(parsed.get("compose"))

    signer_key_id = parsed.get("signer_key_id")
    if signer_key_id != pinned.key_id:
        _fail("RELEASE0_CANDIDATE_SIGNER_MISMATCH")
    signature_text = parsed.get("signature_base64")
    if type(signature_text) is not str:
        _fail("RELEASE0_CANDIDATE_SIGNATURE_INVALID")
    try:
        signature = base64.b64decode(signature_text.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail("RELEASE0_CANDIDATE_SIGNATURE_INVALID")
    if len(signature) != 64:
        _fail("RELEASE0_CANDIDATE_SIGNATURE_INVALID")
    unsigned = dict(parsed)
    del unsigned["signature_base64"]
    try:
        Ed25519PublicKey.from_public_bytes(pinned.public_key).verify(
            signature,
            _SIGNING_DOMAIN + canonical_release0_immutable_candidate_json_bytes(unsigned),
        )
    except (InvalidSignature, TypeError, ValueError, RecursionError):
        _fail("RELEASE0_CANDIDATE_SIGNATURE_INVALID")

    candidate = Release0ImmutableCandidate(
        candidate_id=candidate_id,
        application=application,
        control=control,
        app_image=app_image,
        bot_image=bot_image,
        term_contract=term_contract,
        critical_source_files=critical_files,
        fi_writer_compose_sha256=fi_compose_sha,
        ir_promoted_compose_sha256=ir_compose_sha,
        signer_key_id=signer_key_id,
        identity_sha256=hashlib.sha256(document).hexdigest(),
    )
    object.__setattr__(candidate, "_verification_capability", _VERIFICATION_CAPABILITY)
    _VERIFIED_STATES[candidate] = _candidate_state(candidate)
    return candidate
