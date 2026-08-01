"""Non-authorizing evidence for a term-fenced application source release.

This is deliberately a small seam between two already separate concerns:

* :mod:`core.fenced_fi_release_identity` binds a signed release claim to
  immutable app/bot image identities; and
* the application itself must prove that its API and bot check a live Writer
  Witness term before startup side effects.

The evidence parsed here says only that a particular clean Git tree passed a
reviewed source-capability inspection.  It is *not* a writer permit, image
signature, deployment authorization, promotion proof, or Full-Matrix result.
An eventual signed release identity may bind this document's SHA-256 and the
image provenance independently.  Keeping this object non-authorizing avoids
turning a build-time check into a distributed-election input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Final, Mapping
from weakref import WeakKeyDictionary


__all__ = (
    "TERM_FENCED_APPLICATION_CAPABILITY_SCHEMA",
    "TERM_FENCED_APPLICATION_CAPABILITY_STATUS",
    "TERM_FENCED_APPLICATION_CAPABILITY_FILES",
    "TERM_FENCED_APPLICATION_CAPABILITIES",
    "TERM_FENCED_IMAGE_LABEL_EVIDENCE_SHA256",
    "TERM_FENCED_IMAGE_LABEL_SOURCE_TREE",
    "TermFencedApplicationCapability",
    "TermFencedApplicationCapabilityError",
    "canonical_term_fenced_application_capability_json_bytes",
    "expected_term_fenced_image_labels",
    "require_verified_term_fenced_application_capability",
    "verify_term_fenced_application_capability",
    "verify_term_fenced_image_labels",
)


TERM_FENCED_APPLICATION_CAPABILITY_SCHEMA: Final = (
    "gold-trade-term-fenced-application-capability-v1"
)
TERM_FENCED_APPLICATION_CAPABILITY_STATUS: Final = "source-capability-evidence-only"

# These paths are intentionally closed and small.  The source verifier reads
# their immutable Git blobs, then the evidence binds their exact bytes.
TERM_FENCED_APPLICATION_CAPABILITY_FILES: Final = frozenset(
    {
        "bot/middlewares/writer_term.py",
        "bot/writer_readiness.py",
        "core/application_writer_term.py",
        "core/config.py",
        "core/db.py",
        "main.py",
        "run_bot.py",
    }
)
TERM_FENCED_APPLICATION_CAPABILITIES: Final = tuple(
    sorted(
        {
            "api-live-term-before-runtime-dependencies",
            "bot-live-term-before-runtime-dependencies",
            "bot-update-term-middleware-first",
            "bot-readiness-bound-to-live-term",
            "database-writes-and-bootstrap-term-fenced",
            "runtime-config-requires-single-writer-and-no-schema-bootstrap",
        }
    )
)

# OCI labels are a convenient build output contract.  They supplement, but do
# not replace, the separately signed image ID/repository-digest identity.
TERM_FENCED_IMAGE_LABEL_EVIDENCE_SHA256: Final = (
    "org.goldtrade.term-fence-evidence-sha256"
)
TERM_FENCED_IMAGE_LABEL_SOURCE_TREE: Final = "org.goldtrade.source-tree"

_SHA1_RE: Final = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_DOCUMENT_FIELDS: Final = frozenset(
    {
        "schema",
        "status",
        "release_sha",
        "release_tree_sha",
        "source_files",
        "capabilities",
        "writer_authorized",
        "promotion_authorized",
        "deployment_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
    }
)
_CAPABILITY: Final = object()
_STATES: WeakKeyDictionary["TermFencedApplicationCapability", tuple[object, ...]] = (
    WeakKeyDictionary()
)


class TermFencedApplicationCapabilityError(ValueError):
    """The source-capability evidence is malformed or not safe to use."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise TermFencedApplicationCapabilityError(code)


def canonical_term_fenced_application_capability_json_bytes(value: object) -> bytes:
    """Return the closed canonical encoding used by this evidence only."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise TermFencedApplicationCapabilityError(
            "TERM_FENCED_APPLICATION_CAPABILITY_DOCUMENT_INVALID"
        ) from exc


@dataclass(frozen=True, eq=False)
class TermFencedApplicationCapability:
    """Verified, explicitly non-authorizing source-capability evidence."""

    release_sha: str
    release_tree_sha: str
    source_files: tuple[tuple[str, str], ...]
    capabilities: tuple[str, ...]
    evidence_sha256: str
    writer_authorized: bool = False
    promotion_authorized: bool = False
    deployment_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("TERM_FENCED_APPLICATION_CAPABILITY_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("TERM_FENCED_APPLICATION_CAPABILITY_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("TERM_FENCED_APPLICATION_CAPABILITY_COPY_FORBIDDEN")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("TERM_FENCED_APPLICATION_CAPABILITY_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    _fail("TERM_FENCED_APPLICATION_CAPABILITY_JSON_CONSTANT_FORBIDDEN")


def _sha1(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA1_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _source_files(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping) or set(value) != TERM_FENCED_APPLICATION_CAPABILITY_FILES:
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_SOURCE_FILES_INVALID")
    normalized: list[tuple[str, str]] = []
    for name in sorted(TERM_FENCED_APPLICATION_CAPABILITY_FILES):
        normalized.append(
            (
                name,
                _sha256(
                    value.get(name),
                    code="TERM_FENCED_APPLICATION_CAPABILITY_SOURCE_FILES_INVALID",
                ),
            )
        )
    return tuple(normalized)


def _capabilities(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(type(item) is not str for item in value)
        or tuple(value) != TERM_FENCED_APPLICATION_CAPABILITIES
    ):
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_CAPABILITIES_INVALID")
    return tuple(value)


def _non_authorizing(value: Mapping[str, Any]) -> None:
    for field_name in (
        "writer_authorized",
        "promotion_authorized",
        "deployment_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
    ):
        if value.get(field_name) is not False:
            _fail("TERM_FENCED_APPLICATION_CAPABILITY_AUTHORIZATION_FORBIDDEN")


def verify_term_fenced_application_capability(
    document: bytes,
) -> TermFencedApplicationCapability:
    """Parse one exact canonical non-authorizing evidence document."""

    if type(document) is not bytes or not document or len(document) > 64 * 1024:
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_DOCUMENT_INVALID")
    try:
        parsed = json.loads(
            document.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except TermFencedApplicationCapabilityError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError, RecursionError):
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_DOCUMENT_INVALID")
    if not isinstance(parsed, dict) or set(parsed) != _DOCUMENT_FIELDS:
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_FIELDS_INVALID")
    try:
        canonical = canonical_term_fenced_application_capability_json_bytes(parsed)
    except TermFencedApplicationCapabilityError:
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_DOCUMENT_INVALID")
    if canonical != document:
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_CANONICAL_REQUIRED")
    if parsed.get("schema") != TERM_FENCED_APPLICATION_CAPABILITY_SCHEMA:
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_SCHEMA_INVALID")
    if parsed.get("status") != TERM_FENCED_APPLICATION_CAPABILITY_STATUS:
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_STATUS_INVALID")
    release_sha = _sha1(
        parsed.get("release_sha"),
        code="TERM_FENCED_APPLICATION_CAPABILITY_RELEASE_INVALID",
    )
    release_tree_sha = _sha1(
        parsed.get("release_tree_sha"),
        code="TERM_FENCED_APPLICATION_CAPABILITY_TREE_INVALID",
    )
    source_files = _source_files(parsed.get("source_files"))
    capabilities = _capabilities(parsed.get("capabilities"))
    _non_authorizing(parsed)
    result = TermFencedApplicationCapability(
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        source_files=source_files,
        capabilities=capabilities,
        evidence_sha256=hashlib.sha256(document).hexdigest(),
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = (
        result.release_sha,
        result.release_tree_sha,
        result.source_files,
        result.capabilities,
        result.evidence_sha256,
    )
    return result


def require_verified_term_fenced_application_capability(
    value: object,
) -> TermFencedApplicationCapability:
    """Accept only evidence created by :func:`verify...` in this process."""

    if (
        type(value) is not TermFencedApplicationCapability
        or value._capability is not _CAPABILITY
        or value not in _STATES
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.deployment_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_UNVERIFIED")
    if _STATES[value] != (
        value.release_sha,
        value.release_tree_sha,
        value.source_files,
        value.capabilities,
        value.evidence_sha256,
    ):
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_UNVERIFIED")
    return value


def expected_term_fenced_image_labels(
    evidence: TermFencedApplicationCapability,
) -> dict[str, str]:
    """Return the mandatory non-secret build labels for either app or bot.

    The caller must still pin the image by repository digest and image ID in a
    signed release identity.  These labels merely make the checked source
    capability visible at the image boundary.
    """

    verified = require_verified_term_fenced_application_capability(evidence)
    return {
        "org.opencontainers.image.revision": verified.release_sha,
        TERM_FENCED_IMAGE_LABEL_SOURCE_TREE: verified.release_tree_sha,
        TERM_FENCED_IMAGE_LABEL_EVIDENCE_SHA256: verified.evidence_sha256,
    }


def verify_term_fenced_image_labels(
    labels: Mapping[str, str],
    *,
    evidence: TermFencedApplicationCapability,
) -> None:
    """Require the image's capability labels to equal verified source proof.

    This intentionally returns ``None``: equality of labels grants no
    execution authority and cannot be used as a writer permit.
    """

    if not isinstance(labels, Mapping):
        _fail("TERM_FENCED_APPLICATION_CAPABILITY_IMAGE_LABELS_INVALID")
    expected = expected_term_fenced_image_labels(evidence)
    for key, expected_value in expected.items():
        if labels.get(key) != expected_value:
            _fail("TERM_FENCED_APPLICATION_CAPABILITY_IMAGE_LABEL_MISMATCH")
