"""Pure verifier for one signed, immutable WA-FI fenced-release identity.

This module is intentionally not a release installer, Compose runner, image
loader, or writer permit.  It validates the identity that a future root-owned
FI cutover boundary must bind before it can start the fenced app and bot.
In particular, an environment variable, mutable tag, or source SHA alone is
never a release identity.
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
    "canonical_fenced_fi_release_identity_json_bytes",
    "FENCED_FI_RELEASE_IDENTITY_SCHEMA",
    "FENCED_FI_RELEASE_IDENTITY_LEGACY_SCHEMA",
    "FencedFiReleaseIdentity",
    "FencedFiReleaseIdentityAuthority",
    "FencedFiReleaseIdentityError",
    "require_term_fenced_fi_release_candidate",
    "require_verified_fenced_fi_release_identity",
    "verify_fenced_fi_release_identity",
)


# v1 deliberately remains parseable for read-only inventory/audit tooling, but
# it can never be admitted by the writer preflight.  A term-fenced candidate
# must use v2: its signature binds the source-capability evidence digest in
# addition to the immutable source tree and image identities.
FENCED_FI_RELEASE_IDENTITY_SCHEMA: Final = "gold-trade-wa-fi-fenced-release-identity-v2"
FENCED_FI_RELEASE_IDENTITY_LEGACY_SCHEMA: Final = (
    "gold-trade-wa-fi-fenced-release-identity-v1"
)
_SIGNING_DOMAINS: Final = {
    FENCED_FI_RELEASE_IDENTITY_LEGACY_SCHEMA: (
        b"gold-trade-wa-fi-fenced-release-identity-v1\x00"
    ),
    FENCED_FI_RELEASE_IDENTITY_SCHEMA: (
        b"gold-trade-wa-fi-fenced-release-identity-v2\x00"
    ),
}
_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IMAGE_ID_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_IMAGE_DIGEST_RE: Final = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,511}@sha256:[0-9a-f]{64}$", re.ASCII
)
_KEY_ID_RE: Final = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_PATH_RE: Final = re.compile(r"^/[A-Za-z0-9._/-]+$", re.ASCII)
_RELATIVE_COMPOSE_RE: Final = re.compile(
    r"^deploy/production/docker-compose\.webapp-fi-writer-[A-Za-z0-9._-]+\.yml$",
    re.ASCII,
)
_BASE_DOCUMENT_FIELDS: Final = frozenset(
    {
        "schema",
        "release_sha",
        "release_tree_sha",
        "application_release_root",
        "control_release_sha",
        "control_release_tree_sha",
        "control_release_root",
        "compose_relative_path",
        "compose_sha256",
        "services",
        "signer_key_id",
        "signature_base64",
    }
)
_LEGACY_DOCUMENT_FIELDS: Final = _BASE_DOCUMENT_FIELDS
_TERM_FENCED_DOCUMENT_FIELDS: Final = _BASE_DOCUMENT_FIELDS | frozenset(
    {"term_fenced_application_evidence_sha256"}
)
_VERIFIED_IDENTITY_CAPABILITY: Final = object()
_VERIFIED_IDENTITY_STATES: WeakKeyDictionary["FencedFiReleaseIdentity", tuple[object, ...]] = WeakKeyDictionary()
_SERVICE_FIELDS: Final = frozenset({"image_repo_digest", "image_id"})
_SERVICE_NAMES: Final = frozenset({"app", "bot"})


class FencedFiReleaseIdentityError(ValueError):
    """The descriptor cannot bind a fenced FI release safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise FencedFiReleaseIdentityError(code)


def canonical_fenced_fi_release_identity_json_bytes(value: object) -> bytes:
    """Return the closed ASCII encoding used by this identity only.

    Release identity verification is deliberately independent of the later
    Object-Delta data plane.  Its canonical encoding must therefore live at
    this boundary rather than importing a future transport module which is
    absent from the fixed Release-0 baseline.
    """

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise FencedFiReleaseIdentityError(
            "FENCED_FI_RELEASE_IDENTITY_DOCUMENT_INVALID"
        ) from exc


@dataclass(frozen=True)
class FencedFiReleaseIdentityAuthority:
    """Pinned public authority, supplied only by a root-owned caller."""

    public_key: bytes = b""
    key_id: str = ""


@dataclass(frozen=True, eq=False)
class FencedFiReleaseIdentity:
    """Verified immutable identity; it never authorizes a writer or a phase."""

    schema: str
    release_sha: str
    release_tree_sha: str
    application_release_root: str
    control_release_sha: str
    control_release_tree_sha: str
    control_release_root: str
    compose_relative_path: str
    compose_sha256: str
    app_image_repo_digest: str
    app_image_id: str
    bot_image_repo_digest: str
    bot_image_id: str
    signer_key_id: str
    term_fenced_application_evidence_sha256: str | None
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
        raise TypeError("FENCED_FI_RELEASE_IDENTITY_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("FENCED_FI_RELEASE_IDENTITY_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("FENCED_FI_RELEASE_IDENTITY_COPY_FORBIDDEN")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("FENCED_FI_RELEASE_IDENTITY_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    _fail("FENCED_FI_RELEASE_IDENTITY_JSON_CONSTANT_FORBIDDEN")


def _sha(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _absolute_release_root(value: object, *, release_sha: str, code: str) -> str:
    if type(value) is not str or _PATH_RE.fullmatch(value) is None:
        _fail(code)
    pieces = value.split("/")
    if ".." in pieces or value.rstrip("/").rsplit("/", 1)[-1] != release_sha:
        _fail(code)
    return value


def _authority(value: object) -> FencedFiReleaseIdentityAuthority:
    if type(value) is not FencedFiReleaseIdentityAuthority:
        _fail("FENCED_FI_RELEASE_IDENTITY_AUTHORITY_INVALID")
    if type(value.public_key) is not bytes or len(value.public_key) != 32:
        _fail("FENCED_FI_RELEASE_IDENTITY_AUTHORITY_INVALID")
    if type(value.key_id) is not str or _KEY_ID_RE.fullmatch(value.key_id) is None:
        _fail("FENCED_FI_RELEASE_IDENTITY_AUTHORITY_INVALID")
    if hashlib.sha256(value.public_key).hexdigest() != value.key_id.removeprefix("ed25519-sha256:"):
        _fail("FENCED_FI_RELEASE_IDENTITY_AUTHORITY_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(value.public_key)
    except ValueError:
        _fail("FENCED_FI_RELEASE_IDENTITY_AUTHORITY_INVALID")
    return value


def _service(value: object, *, name: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != _SERVICE_FIELDS:
        _fail("FENCED_FI_RELEASE_IDENTITY_SERVICE_INVALID")
    digest = value.get("image_repo_digest")
    image_id = value.get("image_id")
    if type(digest) is not str or _IMAGE_DIGEST_RE.fullmatch(digest) is None:
        _fail("FENCED_FI_RELEASE_IDENTITY_SERVICE_INVALID")
    if type(image_id) is not str or _IMAGE_ID_RE.fullmatch(image_id) is None:
        _fail("FENCED_FI_RELEASE_IDENTITY_SERVICE_INVALID")
    del name
    return digest, image_id


def _document_fields_for_schema(schema: object) -> frozenset[str]:
    if schema == FENCED_FI_RELEASE_IDENTITY_LEGACY_SCHEMA:
        return _LEGACY_DOCUMENT_FIELDS
    if schema == FENCED_FI_RELEASE_IDENTITY_SCHEMA:
        return _TERM_FENCED_DOCUMENT_FIELDS
    _fail("FENCED_FI_RELEASE_IDENTITY_SCHEMA_INVALID")


def _signature_domain_for_schema(schema: str) -> bytes:
    try:
        return _SIGNING_DOMAINS[schema]
    except KeyError:  # Defensive: the schema was already closed above.
        _fail("FENCED_FI_RELEASE_IDENTITY_SCHEMA_INVALID")


def require_verified_fenced_fi_release_identity(value: object) -> FencedFiReleaseIdentity:
    """Accept only an identity minted by this verifier in this process."""

    if (
        type(value) is not FencedFiReleaseIdentity
        or value._verification_capability is not _VERIFIED_IDENTITY_CAPABILITY
        or value not in _VERIFIED_IDENTITY_STATES
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("FENCED_FI_RELEASE_IDENTITY_UNVERIFIED")
    expected = _VERIFIED_IDENTITY_STATES[value]
    actual = (
        value.schema, value.release_sha, value.release_tree_sha, value.application_release_root,
        value.control_release_sha, value.control_release_tree_sha, value.control_release_root,
        value.compose_relative_path, value.compose_sha256, value.app_image_repo_digest,
        value.app_image_id, value.bot_image_repo_digest, value.bot_image_id,
        value.signer_key_id, value.term_fenced_application_evidence_sha256,
        value.identity_sha256,
    )
    if actual != expected:
        _fail("FENCED_FI_RELEASE_IDENTITY_UNVERIFIED")
    return value


def require_term_fenced_fi_release_candidate(value: object) -> FencedFiReleaseIdentity:
    """Require the v2 signed identity required for an executable FI candidate.

    This is intentionally stricter than the generic verifier: a historically
    valid v1 descriptor has no source-capability evidence binding and is
    therefore evidence only.  It cannot be upgraded by a local config or an
    image label.
    """

    verified = require_verified_fenced_fi_release_identity(value)
    if (
        verified.schema != FENCED_FI_RELEASE_IDENTITY_SCHEMA
        or type(verified.term_fenced_application_evidence_sha256) is not str
        or _SHA256_RE.fullmatch(verified.term_fenced_application_evidence_sha256) is None
    ):
        _fail("FENCED_FI_RELEASE_IDENTITY_TERM_FENCED_CANDIDATE_REQUIRED")
    return verified


def verify_fenced_fi_release_identity(
    document: bytes,
    *,
    authority: FencedFiReleaseIdentityAuthority,
) -> FencedFiReleaseIdentity:
    """Verify exact canonical signed bytes without any filesystem or runtime call."""

    pinned = _authority(authority)
    if type(document) is not bytes or not document or len(document) > 64 * 1024:
        _fail("FENCED_FI_RELEASE_IDENTITY_DOCUMENT_INVALID")
    try:
        parsed = json.loads(
            document.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except FencedFiReleaseIdentityError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError, RecursionError):
        _fail("FENCED_FI_RELEASE_IDENTITY_DOCUMENT_INVALID")
    if not isinstance(parsed, dict):
        _fail("FENCED_FI_RELEASE_IDENTITY_FIELDS_INVALID")
    schema = parsed.get("schema")
    fields = _document_fields_for_schema(schema)
    if set(parsed) != fields:
        _fail("FENCED_FI_RELEASE_IDENTITY_FIELDS_INVALID")
    try:
        canonical_document = canonical_fenced_fi_release_identity_json_bytes(parsed)
    except FencedFiReleaseIdentityError:
        _fail("FENCED_FI_RELEASE_IDENTITY_DOCUMENT_INVALID")
    if canonical_document != document:
        _fail("FENCED_FI_RELEASE_IDENTITY_CANONICAL_REQUIRED")
    release_sha = _sha(parsed.get("release_sha"), code="FENCED_FI_RELEASE_IDENTITY_RELEASE_INVALID")
    release_tree_sha = _sha(parsed.get("release_tree_sha"), code="FENCED_FI_RELEASE_IDENTITY_TREE_INVALID")
    control_sha = _sha(parsed.get("control_release_sha"), code="FENCED_FI_RELEASE_IDENTITY_CONTROL_INVALID")
    control_tree = _sha(parsed.get("control_release_tree_sha"), code="FENCED_FI_RELEASE_IDENTITY_CONTROL_INVALID")
    app_root = _absolute_release_root(parsed.get("application_release_root"), release_sha=release_sha, code="FENCED_FI_RELEASE_IDENTITY_APPLICATION_ROOT_INVALID")
    control_root = _absolute_release_root(parsed.get("control_release_root"), release_sha=control_sha, code="FENCED_FI_RELEASE_IDENTITY_CONTROL_ROOT_INVALID")
    compose_path = parsed.get("compose_relative_path")
    if type(compose_path) is not str or _RELATIVE_COMPOSE_RE.fullmatch(compose_path) is None:
        _fail("FENCED_FI_RELEASE_IDENTITY_COMPOSE_PATH_INVALID")
    compose_sha = _sha256(parsed.get("compose_sha256"), code="FENCED_FI_RELEASE_IDENTITY_COMPOSE_INVALID")
    term_fenced_evidence_sha256: str | None
    if schema == FENCED_FI_RELEASE_IDENTITY_SCHEMA:
        term_fenced_evidence_sha256 = _sha256(
            parsed.get("term_fenced_application_evidence_sha256"),
            code="FENCED_FI_RELEASE_IDENTITY_TERM_FENCED_EVIDENCE_INVALID",
        )
    else:
        term_fenced_evidence_sha256 = None
    services = parsed.get("services")
    if not isinstance(services, Mapping) or set(services) != _SERVICE_NAMES:
        _fail("FENCED_FI_RELEASE_IDENTITY_SERVICE_SET_INVALID")
    app_digest, app_id = _service(services.get("app"), name="app")
    bot_digest, bot_id = _service(services.get("bot"), name="bot")
    signer_key_id = parsed.get("signer_key_id")
    if signer_key_id != pinned.key_id:
        _fail("FENCED_FI_RELEASE_IDENTITY_SIGNER_MISMATCH")
    signature_text = parsed.get("signature_base64")
    if type(signature_text) is not str:
        _fail("FENCED_FI_RELEASE_IDENTITY_SIGNATURE_INVALID")
    try:
        signature = base64.b64decode(signature_text.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail("FENCED_FI_RELEASE_IDENTITY_SIGNATURE_INVALID")
    if len(signature) != 64:
        _fail("FENCED_FI_RELEASE_IDENTITY_SIGNATURE_INVALID")
    unsigned = dict(parsed)
    del unsigned["signature_base64"]
    try:
        signed_payload = (
            _signature_domain_for_schema(schema)
            + canonical_fenced_fi_release_identity_json_bytes(unsigned)
        )
        Ed25519PublicKey.from_public_bytes(pinned.public_key).verify(
            signature,
            signed_payload,
        )
    except (InvalidSignature, TypeError, ValueError, RecursionError):
        _fail("FENCED_FI_RELEASE_IDENTITY_SIGNATURE_INVALID")
    identity = FencedFiReleaseIdentity(
        schema=schema,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        application_release_root=app_root,
        control_release_sha=control_sha,
        control_release_tree_sha=control_tree,
        control_release_root=control_root,
        compose_relative_path=compose_path,
        compose_sha256=compose_sha,
        app_image_repo_digest=app_digest,
        app_image_id=app_id,
        bot_image_repo_digest=bot_digest,
        bot_image_id=bot_id,
        signer_key_id=signer_key_id,
        term_fenced_application_evidence_sha256=term_fenced_evidence_sha256,
        identity_sha256=hashlib.sha256(document).hexdigest(),
    )
    object.__setattr__(identity, "_verification_capability", _VERIFIED_IDENTITY_CAPABILITY)
    _VERIFIED_IDENTITY_STATES[identity] = (
        identity.schema, identity.release_sha, identity.release_tree_sha, identity.application_release_root,
        identity.control_release_sha, identity.control_release_tree_sha, identity.control_release_root,
        identity.compose_relative_path, identity.compose_sha256, identity.app_image_repo_digest,
        identity.app_image_id, identity.bot_image_repo_digest, identity.bot_image_id,
        identity.signer_key_id, identity.term_fenced_application_evidence_sha256,
        identity.identity_sha256,
    )
    return identity
