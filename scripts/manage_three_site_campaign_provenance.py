#!/usr/bin/env python3
"""Create one immutable, non-authorizing three-site campaign provenance.

This controller-local boundary joins three independently verified inputs only
after they have settled:

* a signed, term-fenced, non-legacy App/Bot candidate identity;
* the exact clean control Git release named by that candidate; and
* the committed current Writer Witness policy/selector/activation-ledger head.

It does not create a candidate, contact a peer, Object Storage, a registry, or
the Witness, and it does not invoke Docker or change a service.  Its output is
an audit pin, not a writer, promotion, deployment, execution, or Full-Matrix
authorization.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core import fenced_fi_release_identity as candidate_identity  # noqa: E402
from scripts import prepare_writer_witness_immutable_release as witness_control  # noqa: E402
from scripts import verify_writer_witness_paired_attestation as witness_pair  # noqa: E402
from scripts import webapp_fi_source_campaign_binding as control_binding  # noqa: E402
from scripts import writer_witness_rotation_lifecycle as witness_lifecycle  # noqa: E402


THREE_SITE_CAMPAIGN_PROVENANCE_SCHEMA = "gold-trade-three-site-campaign-provenance-v1"
THREE_SITE_CAMPAIGN_PROVENANCE_STATUS = "bound-non-authorizing"
CANDIDATE_CLAIM_SCHEMA = "gold-trade-three-site-campaign-candidate-claim-v1"
CAMPAIGN_CLAIM_SCHEMA = "gold-trade-three-site-campaign-claim-v1"
CLAIM_STATUS = "claimed"

PROVENANCE_DIRECTORY_NAME = "three-site-campaign-provenance-v1"
CAMPAIGNS_DIRECTORY_NAME = "campaigns"
CANDIDATE_CLAIMS_DIRECTORY_NAME = "candidate-claims"
CAMPAIGN_CLAIMS_DIRECTORY_NAME = "campaign-claims"
PROVENANCE_FILENAME = "three-site-campaign-provenance.json"
LOCK_FILENAME = "campaign-provenance.lock"

DEFAULT_PROVENANCE_ROOT = (
    Path("/etc/trading-bot-three-site") / PROVENANCE_DIRECTORY_NAME
)
DEFAULT_CANDIDATE_AUTHORITY_PATH = (
    Path("/etc/trading-bot-three-site") / "fenced-fi-release-identity-authority.pub"
)
WITNESS_PROFILE_RELATIVE_PATH = Path(
    "deploy/production/writer-witness-60s-release.json"
)

LEGACY_UNFENCED_APPLICATION_RELEASE_SHA = (
    "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
)
LEGACY_UNFENCED_COMPOSE_RELATIVE_PATH = (
    "deploy/production/docker-compose.webapp-fi-writer-2c08.yml"
)

MAXIMUM_DOCUMENT_BYTES = 64 * 1024
MAXIMUM_CLAIM_BYTES = 16 * 1024
MAXIMUM_AUTHORITY_BYTES = 256
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
SHA1_RE = re.compile(r"^[a-f0-9]{40}$", re.ASCII)
SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
IMAGE_ID_RE = re.compile(r"^sha256:[a-f0-9]{64}$", re.ASCII)
IMAGE_DIGEST_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,511}@sha256:[a-f0-9]{64}$",
    re.ASCII,
)
KEY_ID_RE = re.compile(r"^ed25519-sha256:[a-f0-9]{64}$", re.ASCII)
RELATIVE_COMPOSE_RE = re.compile(
    r"^deploy/production/docker-compose\.webapp-fi-writer-[A-Za-z0-9._-]+\.yml$",
    re.ASCII,
)
NL = bytes((10,))

_PROVENANCE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "control",
        "candidate",
        "witness",
        "writer_authorized",
        "promotion_authorized",
        "deployment_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
        "provenance_sha256",
    }
)
_CONTROL_FIELDS = frozenset({"release_sha", "release_tree_sha"})
_CANDIDATE_FIELDS = frozenset(
    {
        "identity_sha256",
        "schema",
        "application_release_sha",
        "application_release_tree_sha",
        "term_fenced_application_evidence_sha256",
        "compose_relative_path",
        "compose_sha256",
        "signer_key_id",
        "services",
    }
)
_SERVICE_FIELDS = frozenset({"image_repo_digest", "image_id"})
_WITNESS_FIELDS = frozenset(
    {
        "profile_sha256",
        "profile_relative_path",
        "policy_id",
        "policy_sha256",
        "selector_filename",
        "selector_sha256",
        "activation_filename",
        "activation_sha256",
        "ledger_sha256",
        "ledger_entries",
        "sequence",
        "not_before",
        "not_after",
    }
)
_CLAIM_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "candidate_identity_sha256",
        "provenance_sha256",
        "witness_activation_sha256",
    }
)


class ThreeSiteCampaignProvenanceError(RuntimeError):
    """A final campaign provenance input is absent, stale, or unsafe."""


def _fail(code: str) -> None:
    raise ThreeSiteCampaignProvenanceError(code)


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_ROOT_REQUIRED")


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ThreeSiteCampaignProvenanceError(
            "THREE_SITE_CAMPAIGN_PROVENANCE_JSON_INVALID"
        ) from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("THREE_SITE_CAMPAIGN_PROVENANCE_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("THREE_SITE_CAMPAIGN_PROVENANCE_JSON_INVALID")


def _absolute_path(value: Path | str, *, label: str) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str):
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_PATH_INVALID")
    path = Path(raw)
    if (
        not raw
        or "\x00" in raw
        or not path.is_absolute()
        or raw.startswith("//")
        or any(part in {"", ".", ".."} for part in path.parts[1:])
        or raw != os.path.normpath(raw)
    ):
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_PATH_INVALID")
    return path


def _require_safe_ancestor_chain(path: Path, *, label: str) -> None:
    """Require root-controlled lookup ancestors; permit root-owned sticky /tmp."""

    path = _absolute_path(path, label=label)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ThreeSiteCampaignProvenanceError(
                f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_ANCESTOR_UNAVAILABLE"
            ) from exc
        mode = stat.S_IMODE(metadata.st_mode)
        sticky_root_directory = (
            stat.S_ISDIR(metadata.st_mode)
            and metadata.st_uid == 0
            and bool(mode & stat.S_ISVTX)
        )
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or ((mode & 0o022) and not sticky_root_directory)
        ):
            _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_ANCESTOR_UNSAFE")


def _require_root_controlled_directory(path: Path, *, label: str) -> Path:
    path = _absolute_path(path, label=label)
    _require_safe_ancestor_chain(path.parent, label=label)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_UNAVAILABLE"
        ) from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) & 0o022
    ):
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_UNSAFE")
    return resolved


def _require_root_private_directory(path: Path, *, label: str) -> Path:
    path = _require_root_controlled_directory(path, label=label)
    try:
        metadata = path.lstat()
    except OSError as exc:  # pragma: no cover - checked by the helper above.
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_UNAVAILABLE"
        ) from exc
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_UNSAFE")
    return path


def _create_or_require_private_child(parent: Path, name: str, *, label: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name:
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_NAME_INVALID")
    parent = _require_root_private_directory(parent, label=f"{label}_PARENT")
    child = parent / name
    try:
        os.mkdir(child, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_CREATE_FAILED"
        ) from exc
    result = _require_root_private_directory(child, label=label)
    _fsync_directory(parent, label=f"{label}_PARENT")
    return result


def _create_private_campaign_directory(parent: Path, campaign_id: str) -> Path:
    parent = _require_root_private_directory(parent, label="CAMPAIGNS_DIRECTORY")
    path = parent / campaign_id
    try:
        os.mkdir(path, 0o700)
    except FileExistsError as exc:
        raise ThreeSiteCampaignProvenanceError(
            "THREE_SITE_CAMPAIGN_PROVENANCE_CAMPAIGN_ALREADY_CLAIMED"
        ) from exc
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            "THREE_SITE_CAMPAIGN_PROVENANCE_CAMPAIGN_DIRECTORY_CREATE_FAILED"
        ) from exc
    result = _require_root_private_directory(path, label="CAMPAIGN_DIRECTORY")
    _fsync_directory(parent, label="CAMPAIGNS_DIRECTORY")
    return result


def _fsync_directory(path: Path, *, label: str) -> None:
    path = _require_root_private_directory(path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_OPEN_FAILED"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_CHANGED")
        os.fsync(descriptor)
    except ThreeSiteCampaignProvenanceError:
        raise
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_SYNC_FAILED"
        ) from exc
    finally:
        os.close(descriptor)


def _read_root_only_file(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    path = _absolute_path(path, label=label)
    _require_root_controlled_directory(path.parent, label=f"{label}_PARENT")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_O_NOFOLLOW_REQUIRED")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | no_follow)
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_UNAVAILABLE"
        ) from exc
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
            or mode & 0o077
        ):
            _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_UNSAFE")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_SHORT_READ")
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if len(payload) != before.st_size or any(
            getattr(before, field) != getattr(after, field) for field in identity
        ):
            _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_CHANGED")
        return payload
    finally:
        os.close(descriptor)


def _write_new_root_only_file(path: Path, *, payload: bytes, label: str) -> None:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAXIMUM_DOCUMENT_BYTES:
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_INVALID")
    path = _absolute_path(path, label=label)
    parent = _require_root_private_directory(path.parent, label=f"{label}_PARENT")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_O_NOFOLLOW_REQUIRED")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | no_follow
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_EXISTS"
        ) from exc
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_CREATE_FAILED"
        ) from exc
    try:
        pending = memoryview(payload)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_WRITE_FAILED")
            pending = pending[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    except ThreeSiteCampaignProvenanceError:
        raise
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_WRITE_FAILED"
        ) from exc
    finally:
        os.close(descriptor)
    _fsync_directory(parent, label=f"{label}_PARENT")


@contextlib.contextmanager
def _provenance_lock(root: Path):
    """Serialize check-and-claim without turning a lock into provenance."""

    root = _require_root_private_directory(root, label="PROVENANCE_ROOT")
    path = root / LOCK_FILENAME
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if type(no_follow) is not int:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_O_NOFOLLOW_REQUIRED")
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | no_follow,
            0o600,
        )
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            "THREE_SITE_CAMPAIGN_PROVENANCE_LOCK_UNAVAILABLE"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("THREE_SITE_CAMPAIGN_PROVENANCE_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except ThreeSiteCampaignProvenanceError:
        raise
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            "THREE_SITE_CAMPAIGN_PROVENANCE_LOCK_UNAVAILABLE"
        ) from exc
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _require_missing(path: Path, *, label: str) -> None:
    path = _absolute_path(path, label=label)
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_UNAVAILABLE"
        ) from exc
    _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_EXISTS")


def _parse_canonical_json(payload: bytes, *, label: str, maximum_bytes: int) -> dict[str, Any]:
    if (
        not isinstance(payload, bytes)
        or not 1 <= len(payload) <= maximum_bytes
        or not payload.endswith(NL)
    ):
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_INVALID")
    try:
        value = json.loads(
            payload[:-1].decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ThreeSiteCampaignProvenanceError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise ThreeSiteCampaignProvenanceError(
            f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_INVALID"
        ) from exc
    if not isinstance(value, dict) or _canonical_json_bytes(value) + NL != payload:
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_INVALID")
    return value


def _require_campaign_id(value: object) -> str:
    if not isinstance(value, str) or CAMPAIGN_ID_RE.fullmatch(value) is None:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CAMPAIGN_ID_INVALID")
    return value


def _require_sha1(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA1_RE.fullmatch(value) is None:
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_INVALID")
    return value


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_INVALID")
    return value


def _require_int(value: object, *, label: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"THREE_SITE_CAMPAIGN_PROVENANCE_{label}_INVALID")
    return value


def _load_candidate_authority(path: Path) -> candidate_identity.FencedFiReleaseIdentityAuthority:
    raw = _read_root_only_file(
        path,
        label="CANDIDATE_AUTHORITY",
        maximum_bytes=MAXIMUM_AUTHORITY_BYTES,
    )
    try:
        text = raw.decode("ascii")
        if text.endswith("\n"):
            text = text[:-1]
        if not text or text != text.strip() or "\n" in text:
            raise ValueError
        public_key = base64.b64decode(text.encode("ascii"), validate=True)
        if base64.b64encode(public_key).decode("ascii") != text or len(public_key) != 32:
            raise ValueError
    except (UnicodeDecodeError, ValueError) as exc:
        raise ThreeSiteCampaignProvenanceError(
            "THREE_SITE_CAMPAIGN_PROVENANCE_CANDIDATE_AUTHORITY_INVALID"
        ) from exc
    return candidate_identity.FencedFiReleaseIdentityAuthority(
        public_key=public_key,
        key_id="ed25519-sha256:" + hashlib.sha256(public_key).hexdigest(),
    )


def _load_nonlegacy_candidate(
    path: Path,
    *,
    authority: candidate_identity.FencedFiReleaseIdentityAuthority,
) -> candidate_identity.FencedFiReleaseIdentity:
    raw = _read_root_only_file(
        path,
        label="CANDIDATE_DESCRIPTOR",
        maximum_bytes=MAXIMUM_DOCUMENT_BYTES,
    )
    try:
        verified = candidate_identity.verify_fenced_fi_release_identity(raw, authority=authority)
        candidate = candidate_identity.require_term_fenced_fi_release_candidate(verified)
    except candidate_identity.FencedFiReleaseIdentityError as exc:
        raise ThreeSiteCampaignProvenanceError(
            "THREE_SITE_CAMPAIGN_PROVENANCE_CANDIDATE_DESCRIPTOR_INVALID"
        ) from exc
    if (
        candidate.release_sha == LEGACY_UNFENCED_APPLICATION_RELEASE_SHA
        or candidate.compose_relative_path == LEGACY_UNFENCED_COMPOSE_RELATIVE_PATH
    ):
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_LEGACY_2C08_CANDIDATE_BLOCKED")
    if candidate.identity_sha256 != _sha256(raw):  # Defensive invariant of the pure verifier.
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CANDIDATE_DESCRIPTOR_INVALID")
    return candidate


def _verify_exact_control_release(
    candidate: candidate_identity.FencedFiReleaseIdentity,
) -> dict[str, str]:
    """Derive the control tree from the candidate's exact signed local root.

    The shared checkout verifier allows only bounded local Git inspection.  It
    never fetches, checks out, runs hooks, or accesses a network remote.
    """

    try:
        observed = control_binding._verify_clean_detached_checkout(
            repository=Path(candidate.control_release_root),
            expected_commit=candidate.control_release_sha,
            field="candidate control release",
        )
    except control_binding.CampaignBindingError as exc:
        raise ThreeSiteCampaignProvenanceError(
            "THREE_SITE_CAMPAIGN_PROVENANCE_CONTROL_RELEASE_INVALID"
        ) from exc
    if observed.tree != candidate.control_release_tree_sha:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CONTROL_RELEASE_TREE_MISMATCH")
    return {
        "release_sha": observed.commit,
        "release_tree_sha": observed.tree,
    }


def _normalise_time(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_VERIFICATION_TIME_INVALID")
    return now.astimezone(timezone.utc)


def _load_final_witness_identity(
    *,
    state_directory: Path | None,
    control_release_root: Path,
    verification_time: datetime,
) -> dict[str, Any]:
    try:
        # The profile is part of the already verified exact control tree, not
        # an ambient file from whichever checkout happened to launch this
        # command.  The clean detached checkout check preceding this call
        # makes this fixed relative path part of `control.release_tree_sha`.
        profile = witness_control._load_profile(
            control_release_root / WITNESS_PROFILE_RELATIVE_PATH
        )
        profile_sha256 = witness_pair._profile_sha256(profile)
        selected = witness_lifecycle.resolve_current_policy(
            profile_sha256=profile_sha256,
            state_directory=state_directory,
        )
        policy = witness_pair._load_rotation_policy(selected.policy_raw, profile=profile)
    except (
        witness_control.WitnessReleasePreparationError,
        witness_lifecycle.WriterWitnessRotationLifecycleError,
        witness_pair.WriterWitnessPairAttestationError,
    ) as exc:
        raise ThreeSiteCampaignProvenanceError(
            "THREE_SITE_CAMPAIGN_PROVENANCE_WITNESS_LIFECYCLE_INVALID"
        ) from exc
    if (
        selected.profile_sha256 != profile_sha256
        or policy["policy_id"] != selected.policy_id
        or policy["sha256"] != selected.policy_sha256
        or verification_time < policy["not_before"]
        or verification_time >= policy["not_after"]
    ):
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_WITNESS_POLICY_STALE")
    return {
        "profile_sha256": selected.profile_sha256,
        "profile_relative_path": WITNESS_PROFILE_RELATIVE_PATH.as_posix(),
        "policy_id": selected.policy_id,
        "policy_sha256": selected.policy_sha256,
        "selector_filename": selected.selector_filename,
        "selector_sha256": selected.selector_sha256,
        "activation_filename": selected.activation_filename,
        "activation_sha256": selected.activation_sha256,
        "ledger_sha256": selected.ledger_sha256,
        "ledger_entries": selected.ledger_entries,
        "sequence": selected.sequence,
        "not_before": policy["not_before"].isoformat(),
        "not_after": policy["not_after"].isoformat(),
    }


def _candidate_value(candidate: candidate_identity.FencedFiReleaseIdentity) -> dict[str, Any]:
    evidence = candidate.term_fenced_application_evidence_sha256
    if not isinstance(evidence, str) or SHA256_RE.fullmatch(evidence) is None:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CANDIDATE_DESCRIPTOR_INVALID")
    return {
        "identity_sha256": candidate.identity_sha256,
        "schema": candidate.schema,
        "application_release_sha": candidate.release_sha,
        "application_release_tree_sha": candidate.release_tree_sha,
        "term_fenced_application_evidence_sha256": evidence,
        "compose_relative_path": candidate.compose_relative_path,
        "compose_sha256": candidate.compose_sha256,
        "signer_key_id": candidate.signer_key_id,
        "services": {
            "app": {
                "image_repo_digest": candidate.app_image_repo_digest,
                "image_id": candidate.app_image_id,
            },
            "bot": {
                "image_repo_digest": candidate.bot_image_repo_digest,
                "image_id": candidate.bot_image_id,
            },
        },
    }


def _unsigned_provenance(
    *,
    campaign_id: str,
    control: Mapping[str, str],
    candidate: Mapping[str, Any],
    witness: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": THREE_SITE_CAMPAIGN_PROVENANCE_SCHEMA,
        "status": THREE_SITE_CAMPAIGN_PROVENANCE_STATUS,
        "campaign_id": _require_campaign_id(campaign_id),
        "control": dict(control),
        "candidate": dict(candidate),
        "witness": dict(witness),
        "writer_authorized": False,
        "promotion_authorized": False,
        "deployment_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def _validate_service(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _SERVICE_FIELDS:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CANDIDATE_INVALID")
    digest = value.get("image_repo_digest")
    image_id = value.get("image_id")
    if (
        not isinstance(digest, str)
        or IMAGE_DIGEST_RE.fullmatch(digest) is None
        or "://" in digest
        or not isinstance(image_id, str)
        or IMAGE_ID_RE.fullmatch(image_id) is None
    ):
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CANDIDATE_INVALID")
    return {"image_repo_digest": digest, "image_id": image_id}


def _validate_provenance_value(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _PROVENANCE_FIELDS:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_DOCUMENT_INVALID")
    if (
        value.get("schema") != THREE_SITE_CAMPAIGN_PROVENANCE_SCHEMA
        or value.get("status") != THREE_SITE_CAMPAIGN_PROVENANCE_STATUS
    ):
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_DOCUMENT_INVALID")
    campaign_id = _require_campaign_id(value.get("campaign_id"))
    control = value.get("control")
    if not isinstance(control, Mapping) or set(control) != _CONTROL_FIELDS:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CONTROL_INVALID")
    control_value = {
        "release_sha": _require_sha1(control.get("release_sha"), label="CONTROL_RELEASE_SHA"),
        "release_tree_sha": _require_sha1(
            control.get("release_tree_sha"), label="CONTROL_RELEASE_TREE_SHA"
        ),
    }
    candidate = value.get("candidate")
    if not isinstance(candidate, Mapping) or set(candidate) != _CANDIDATE_FIELDS:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CANDIDATE_INVALID")
    if candidate.get("schema") != candidate_identity.FENCED_FI_RELEASE_IDENTITY_SCHEMA:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CANDIDATE_INVALID")
    release_sha = _require_sha1(
        candidate.get("application_release_sha"), label="CANDIDATE_RELEASE_SHA"
    )
    if release_sha == LEGACY_UNFENCED_APPLICATION_RELEASE_SHA:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_LEGACY_2C08_CANDIDATE_BLOCKED")
    candidate_value = {
        "identity_sha256": _require_sha256(
            candidate.get("identity_sha256"), label="CANDIDATE_IDENTITY_SHA256"
        ),
        "schema": candidate_identity.FENCED_FI_RELEASE_IDENTITY_SCHEMA,
        "application_release_sha": release_sha,
        "application_release_tree_sha": _require_sha1(
            candidate.get("application_release_tree_sha"), label="CANDIDATE_RELEASE_TREE_SHA"
        ),
        "term_fenced_application_evidence_sha256": _require_sha256(
            candidate.get("term_fenced_application_evidence_sha256"),
            label="CANDIDATE_TERM_FENCED_EVIDENCE_SHA256",
        ),
        "compose_relative_path": candidate.get("compose_relative_path"),
        "compose_sha256": _require_sha256(
            candidate.get("compose_sha256"), label="CANDIDATE_COMPOSE_SHA256"
        ),
        "signer_key_id": candidate.get("signer_key_id"),
        "services": candidate.get("services"),
    }
    if (
        not isinstance(candidate_value["compose_relative_path"], str)
        or RELATIVE_COMPOSE_RE.fullmatch(candidate_value["compose_relative_path"]) is None
        or not isinstance(candidate_value["signer_key_id"], str)
        or KEY_ID_RE.fullmatch(candidate_value["signer_key_id"]) is None
        or not isinstance(candidate_value["services"], Mapping)
        or set(candidate_value["services"]) != {"app", "bot"}
    ):
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CANDIDATE_INVALID")
    if candidate_value["compose_relative_path"] == LEGACY_UNFENCED_COMPOSE_RELATIVE_PATH:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_LEGACY_2C08_CANDIDATE_BLOCKED")
    candidate_value["services"] = {
        "app": _validate_service(candidate_value["services"]["app"]),
        "bot": _validate_service(candidate_value["services"]["bot"]),
    }
    witness = value.get("witness")
    if not isinstance(witness, Mapping) or set(witness) != _WITNESS_FIELDS:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_WITNESS_INVALID")
    policy_id = witness.get("policy_id")
    if (
        not isinstance(policy_id, str)
        or witness_lifecycle.POLICY_ID_RE.fullmatch(policy_id) is None
    ):
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_WITNESS_INVALID")
    selector_filename = witness.get("selector_filename")
    activation_filename = witness.get("activation_filename")
    if (
        not isinstance(selector_filename, str)
        or witness_lifecycle.SELECTOR_FILENAME_RE.fullmatch(selector_filename) is None
        or not isinstance(activation_filename, str)
        or witness_lifecycle.ACTIVATION_FILENAME_RE.fullmatch(activation_filename) is None
    ):
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_WITNESS_INVALID")
    witness_value = {
        "profile_sha256": _require_sha256(
            witness.get("profile_sha256"), label="WITNESS_PROFILE_SHA256"
        ),
        "profile_relative_path": witness.get("profile_relative_path"),
        "policy_id": policy_id,
        "policy_sha256": _require_sha256(
            witness.get("policy_sha256"), label="WITNESS_POLICY_SHA256"
        ),
        "selector_filename": selector_filename,
        "selector_sha256": _require_sha256(
            witness.get("selector_sha256"), label="WITNESS_SELECTOR_SHA256"
        ),
        "activation_filename": activation_filename,
        "activation_sha256": _require_sha256(
            witness.get("activation_sha256"), label="WITNESS_ACTIVATION_SHA256"
        ),
        "ledger_sha256": _require_sha256(
            witness.get("ledger_sha256"), label="WITNESS_LEDGER_SHA256"
        ),
        "ledger_entries": _require_int(
            witness.get("ledger_entries"), label="WITNESS_LEDGER_ENTRIES", minimum=1
        ),
        "sequence": _require_int(witness.get("sequence"), label="WITNESS_SEQUENCE", minimum=1),
        "not_before": witness.get("not_before"),
        "not_after": witness.get("not_after"),
    }
    if witness_value["profile_relative_path"] != WITNESS_PROFILE_RELATIVE_PATH.as_posix():
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_WITNESS_INVALID")
    if not isinstance(witness_value["not_before"], str) or not isinstance(
        witness_value["not_after"], str
    ):
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_WITNESS_INVALID")
    try:
        not_before = datetime.fromisoformat(
            witness_value["not_before"].replace("Z", "+00:00")
        )
        not_after = datetime.fromisoformat(
            witness_value["not_after"].replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ThreeSiteCampaignProvenanceError(
            "THREE_SITE_CAMPAIGN_PROVENANCE_WITNESS_INVALID"
        ) from exc
    if (
        not_before.tzinfo is None
        or not_after.tzinfo is None
        or not_after <= not_before
        or witness_value["ledger_entries"] != witness_value["sequence"]
        or witness_value["selector_filename"]
        != witness_lifecycle.selector_filename(
            sequence=witness_value["sequence"],
            selector_sha256=witness_value["selector_sha256"],
        )
        or witness_value["activation_filename"]
        != witness_lifecycle.activation_filename(
            sequence=witness_value["sequence"],
            activation_sha256=witness_value["activation_sha256"],
        )
    ):
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_WITNESS_INVALID")
    for name in (
        "writer_authorized",
        "promotion_authorized",
        "deployment_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
    ):
        if value.get(name) is not False:
            _fail("THREE_SITE_CAMPAIGN_PROVENANCE_AUTHORIZATION_FORBIDDEN")
    unsigned = _unsigned_provenance(
        campaign_id=campaign_id,
        control=control_value,
        candidate=candidate_value,
        witness=witness_value,
    )
    provenance_sha256 = _require_sha256(
        value.get("provenance_sha256"), label="PROVENANCE_SHA256"
    )
    if provenance_sha256 != _sha256(_canonical_json_bytes(unsigned)):
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CHECKSUM_INVALID")
    return {**unsigned, "provenance_sha256": provenance_sha256}


def _build_provenance(
    *,
    campaign_id: str,
    control: Mapping[str, str],
    candidate: candidate_identity.FencedFiReleaseIdentity,
    witness: Mapping[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    unsigned = _unsigned_provenance(
        campaign_id=campaign_id,
        control=control,
        candidate=_candidate_value(candidate),
        witness=witness,
    )
    value = {**unsigned, "provenance_sha256": _sha256(_canonical_json_bytes(unsigned))}
    verified = _validate_provenance_value(value)
    payload = _canonical_json_bytes(verified) + NL
    if b"://" in payload.lower() or b'"url"' in payload.lower() or b"secret" in payload.lower():
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_DOCUMENT_FORBIDDEN_CONTENT")
    return payload, verified


def _claim_document(
    *,
    schema: str,
    campaign_id: str,
    candidate_identity_sha256: str,
    provenance_sha256: str,
    witness_activation_sha256: str,
) -> bytes:
    value = {
        "schema": schema,
        "status": CLAIM_STATUS,
        "campaign_id": campaign_id,
        "candidate_identity_sha256": candidate_identity_sha256,
        "provenance_sha256": provenance_sha256,
        "witness_activation_sha256": witness_activation_sha256,
    }
    if set(value) != _CLAIM_FIELDS:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CLAIM_INVALID")
    for field in ("candidate_identity_sha256", "provenance_sha256", "witness_activation_sha256"):
        _require_sha256(value[field], label="CLAIM_" + field.upper())
    payload = _canonical_json_bytes(value) + NL
    if len(payload) > MAXIMUM_CLAIM_BYTES:
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_CLAIM_INVALID")
    return payload


def _layout(root: Path) -> tuple[Path, Path, Path]:
    root = _require_root_private_directory(root, label="PROVENANCE_ROOT")
    campaigns = _create_or_require_private_child(
        root, CAMPAIGNS_DIRECTORY_NAME, label="CAMPAIGNS_DIRECTORY"
    )
    candidate_claims = _create_or_require_private_child(
        root, CANDIDATE_CLAIMS_DIRECTORY_NAME, label="CANDIDATE_CLAIMS_DIRECTORY"
    )
    campaign_claims = _create_or_require_private_child(
        root, CAMPAIGN_CLAIMS_DIRECTORY_NAME, label="CAMPAIGN_CLAIMS_DIRECTORY"
    )
    return campaigns, candidate_claims, campaign_claims


def _provenance_path(root: Path, campaign_id: str) -> Path:
    root = _require_root_private_directory(root, label="PROVENANCE_ROOT")
    campaigns = _require_root_private_directory(
        root / CAMPAIGNS_DIRECTORY_NAME, label="CAMPAIGNS_DIRECTORY"
    )
    campaign_directory = _require_root_private_directory(
        campaigns / campaign_id, label="CAMPAIGN_DIRECTORY"
    )
    return campaign_directory / PROVENANCE_FILENAME


def create_three_site_campaign_provenance(
    *,
    campaign_id: str,
    candidate_descriptor: Path,
    _provenance_root_for_test: Path | None = None,
    _candidate_authority_path_for_test: Path | None = None,
    _witness_state_directory_for_test: Path | None = None,
    _verification_time_for_test: datetime | None = None,
) -> dict[str, Any]:
    """Bind one final non-authorizing campaign descriptor, exactly once.

    All production locations are fixed.  Underscored replacements exist only
    for isolated unit tests; the CLI cannot redirect the campaign root,
    authority, Witness state, release profile, or clock.
    """

    _require_root()
    campaign_id = _require_campaign_id(campaign_id)
    verification_time = _normalise_time(_verification_time_for_test)
    root = Path(_provenance_root_for_test or DEFAULT_PROVENANCE_ROOT)
    authority_path = Path(
        _candidate_authority_path_for_test or DEFAULT_CANDIDATE_AUTHORITY_PATH
    )
    authority = _load_candidate_authority(authority_path)
    candidate = _load_nonlegacy_candidate(Path(candidate_descriptor), authority=authority)
    control = _verify_exact_control_release(candidate)
    witness = _load_final_witness_identity(
        state_directory=_witness_state_directory_for_test,
        control_release_root=Path(candidate.control_release_root),
        verification_time=verification_time,
    )
    payload, provenance = _build_provenance(
        campaign_id=campaign_id,
        control=control,
        candidate=candidate,
        witness=witness,
    )
    campaigns, candidate_claims, campaign_claims = _layout(root)
    campaign_claim = _claim_document(
        schema=CAMPAIGN_CLAIM_SCHEMA,
        campaign_id=campaign_id,
        candidate_identity_sha256=provenance["candidate"]["identity_sha256"],
        provenance_sha256=provenance["provenance_sha256"],
        witness_activation_sha256=provenance["witness"]["activation_sha256"],
    )
    candidate_claim = _claim_document(
        schema=CANDIDATE_CLAIM_SCHEMA,
        campaign_id=campaign_id,
        candidate_identity_sha256=provenance["candidate"]["identity_sha256"],
        provenance_sha256=provenance["provenance_sha256"],
        witness_activation_sha256=provenance["witness"]["activation_sha256"],
    )
    campaign_claim_path = campaign_claims / f"campaign-{campaign_id}.json"
    candidate_claim_path = (
        candidate_claims / f"candidate-{provenance['candidate']['identity_sha256']}.json"
    )
    campaign_directory_path = campaigns / campaign_id
    # Claims precede the final descriptor. A crash can leave an auditable,
    # fail-closed reservation, but cannot make the candidate reusable by a
    # different campaign. The lock ensures a normal replay attempt creates no
    # unrelated partial claim before its existing claim is detected.
    with _provenance_lock(root):
        _require_missing(candidate_claim_path, label="CANDIDATE_CLAIM")
        _require_missing(campaign_claim_path, label="CAMPAIGN_CLAIM")
        _require_missing(campaign_directory_path, label="CAMPAIGN")
        _write_new_root_only_file(
            candidate_claim_path,
            payload=candidate_claim,
            label="CANDIDATE_CLAIM",
        )
        _write_new_root_only_file(
            campaign_claim_path,
            payload=campaign_claim,
            label="CAMPAIGN_CLAIM",
        )
        campaign_directory = _create_private_campaign_directory(campaigns, campaign_id)
        _write_new_root_only_file(
            campaign_directory / PROVENANCE_FILENAME,
            payload=payload,
            label="DOCUMENT",
        )
    loaded = load_three_site_campaign_provenance(
        campaign_id=campaign_id,
        _provenance_root_for_test=root,
    )
    if loaded != provenance:  # pragma: no cover - defensive post-write invariant.
        _fail("THREE_SITE_CAMPAIGN_PROVENANCE_DOCUMENT_CHANGED")
    return {
        "status": "created-non-authorizing",
        "schema": THREE_SITE_CAMPAIGN_PROVENANCE_SCHEMA,
        "campaign_id": campaign_id,
        "provenance_sha256": provenance["provenance_sha256"],
        "candidate_identity_sha256": provenance["candidate"]["identity_sha256"],
        "control_release_sha": provenance["control"]["release_sha"],
        "control_release_tree_sha": provenance["control"]["release_tree_sha"],
        "witness_activation_sha256": provenance["witness"]["activation_sha256"],
        "witness_ledger_sha256": provenance["witness"]["ledger_sha256"],
        "writer_authorized": False,
        "promotion_authorized": False,
        "deployment_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def load_three_site_campaign_provenance(
    *,
    campaign_id: str,
    _provenance_root_for_test: Path | None = None,
) -> dict[str, Any]:
    """Load one exact immutable campaign provenance descriptor by fixed path."""

    _require_root()
    campaign_id = _require_campaign_id(campaign_id)
    root = Path(_provenance_root_for_test or DEFAULT_PROVENANCE_ROOT)
    path = _provenance_path(root, campaign_id)
    raw = _read_root_only_file(
        path,
        label="DOCUMENT",
        maximum_bytes=MAXIMUM_DOCUMENT_BYTES,
    )
    return _validate_provenance_value(
        _parse_canonical_json(raw, label="DOCUMENT", maximum_bytes=MAXIMUM_DOCUMENT_BYTES)
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser(
        "create",
        help="create one final immutable non-authorizing campaign pin",
    )
    create.add_argument("--campaign-id", required=True)
    create.add_argument("--candidate-descriptor", required=True, type=Path)
    show = actions.add_parser(
        "show",
        help="verify and print one fixed campaign provenance descriptor",
    )
    show.add_argument("--campaign-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.action == "create":
            result = create_three_site_campaign_provenance(
                campaign_id=arguments.campaign_id,
                candidate_descriptor=arguments.candidate_descriptor,
            )
        else:
            value = load_three_site_campaign_provenance(campaign_id=arguments.campaign_id)
            result = {
                "status": "verified-non-authorizing",
                "schema": THREE_SITE_CAMPAIGN_PROVENANCE_SCHEMA,
                "campaign_id": value["campaign_id"],
                "provenance_sha256": value["provenance_sha256"],
                "candidate_identity_sha256": value["candidate"]["identity_sha256"],
                "control_release_sha": value["control"]["release_sha"],
                "witness_activation_sha256": value["witness"]["activation_sha256"],
                "writer_authorized": False,
                "promotion_authorized": False,
                "deployment_authorized": False,
                "execution_authorized": False,
                "full_matrix_authorized": False,
                "full_matrix_executed": False,
            }
    except ThreeSiteCampaignProvenanceError as exc:
        print(
            _canonical_json_bytes(
                {"status": "blocked", "error_class": type(exc).__name__, "error": str(exc)}
            ).decode("ascii"),
            file=sys.stderr,
        )
        return 2
    print(_canonical_json_bytes(result).decode("ascii"))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper.
    raise SystemExit(main())
