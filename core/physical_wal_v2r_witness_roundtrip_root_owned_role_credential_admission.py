"""Fail-closed root-owned local credential admission for one V2R role.

This is deliberately a *local file boundary*, not a transport or provider
adapter.  Before it opens its one fixed file it requires the already verified
V2R host-role admission, provider-route evidence, and signed public bundle.
It then checks that the file is root-owned, private, non-linked, and contains
only the identity pinned by those V2R claims.  It returns an opaque public
admission with no access key, secret, endpoint, bucket, or provider client.

The fixed V2R files are a separate namespace.  No normal-V2 or recovery-data
path is accepted as an argument or imported as a compatibility source.
Nothing here contacts Object Storage, opens a socket, renders IAM, starts a
service, or grants writer/promotion/Phase-5/Full-Matrix authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
from weakref import WeakKeyDictionary

from core import physical_wal_v2r_witness_roundtrip_contract as _v2r
from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as _mailbox
from core import physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission as _bundle
from core import physical_wal_v2r_witness_roundtrip_provider_route_iam_object_lock_evidence as _provider


__all__ = (
    "PHYSICAL_WAL_V2R_ROOT_OWNED_ROLE_CREDENTIAL_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2R_ROOT_OWNED_ROLE_CREDENTIAL_ADMISSION_SCHEMA",
    "PHYSICAL_WAL_V2R_ROOT_OWNED_ROLE_CREDENTIAL_ROOT",
    "PhysicalWalV2rRootOwnedRoleCredentialAdmissionConfig",
    "PhysicalWalV2rRootOwnedRoleCredentialAdmissionError",
    "VerifiedPhysicalWalV2rRootOwnedRoleCredentialAdmission",
    "admit_root_owned_physical_wal_v2r_witness_roundtrip_role_credential",
    "require_verified_root_owned_physical_wal_v2r_witness_roundtrip_role_credential_admission",
)


PHYSICAL_WAL_V2R_ROOT_OWNED_ROLE_CREDENTIAL_ADMISSION_SCHEMA = (
    "gold-trade-physical-wal-v2r-root-owned-role-credential-admission-v1"
)
PHYSICAL_WAL_V2R_ROOT_OWNED_ROLE_CREDENTIAL_ADMISSION_DEFAULT_ENABLED = False
PHYSICAL_WAL_V2R_ROOT_OWNED_ROLE_CREDENTIAL_ROOT = Path(
    "/etc/trading-bot/security/physical-wal-v2r"
)

_FILE_SCHEMA = "gold-trade-physical-wal-v2r-role-credential-v1"
_IDENTITY_DOMAIN = b"gold-trade-physical-wal-v2r-role-credential-identity-v1\x00"
_MAX_BYTES = 16 * 1024
_MAX_VALUE_BYTES = 1024
_VALUE_RE = re.compile(r"^[\x21-\x7e]{1,1024}$", re.ASCII)
_CAPABILITY = object()
_ROLE_FILENAMES = {
    "wa-ir-v2r-exporter": "wa-ir-v2r-exporter.json",
    "witness-v2r-reverse-ingress": "witness-v2r-reverse-ingress.json",
    "witness-v2r-reverse-egress": "witness-v2r-reverse-egress.json",
    "wa-fi-v2r-recovery-inbox": "wa-fi-v2r-recovery-inbox.json",
    "wa-fi-v2r-ack-outbox": "wa-fi-v2r-ack-outbox.json",
    "witness-v2r-ack-ingress": "witness-v2r-ack-ingress.json",
    "witness-v2r-return-egress": "witness-v2r-return-egress.json",
    "wa-ir-v2r-return-inbox": "wa-ir-v2r-return-inbox.json",
}
_POLICY_BY_ROLE = {
    item.local_role: item
    for item in _mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES
}


class PhysicalWalV2rRootOwnedRoleCredentialAdmissionError(ValueError):
    """A fixed V2R credential file or its proofs are unsafe or stale."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2rRootOwnedRoleCredentialAdmissionError(code)


@dataclass(frozen=True)
class PhysicalWalV2rRootOwnedRoleCredentialAdmissionConfig:
    """Default-off typed dependencies for one fixed V2R role file.

    No path, credential, provider endpoint, or normal/recovery identity is a
    caller input.  The contained upstream configs already pin the exact V2R
    role and the twelve legacy identity deny-pins.
    """

    admission_config: (
        _mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig | None
    ) = field(default=None, repr=False, compare=False)
    provider_evidence_config: (
        _provider.PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig | None
    ) = field(default=None, repr=False, compare=False)
    full_bundle_config: (
        _bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig | None
    ) = field(default=None, repr=False, compare=False)
    enabled: bool = PHYSICAL_WAL_V2R_ROOT_OWNED_ROLE_CREDENTIAL_ADMISSION_DEFAULT_ENABLED


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalWalV2rRootOwnedRoleCredentialAdmission:
    """Opaque no-secret local proof; it is never a usable credential."""

    local_site: str
    local_role: str
    mailbox: str
    direction: str
    object_prefix: str
    role_credential_identity_sha256: str
    provider_evidence_sha256: str
    full_bundle_sha256: str
    credential_file_device: int
    credential_file_inode: int
    is_operational: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_authorized: bool = False
    phase5_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object = field(repr=False, compare=False)

    def __init__(self, *, capability: object, **values: Any) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2R_ROOT_OWNED_ROLE_CREDENTIAL_CONSTRUCTION_FORBIDDEN")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2R_ROOT_OWNED_ROLE_CREDENTIAL_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _AdmissionFacts:
    config: PhysicalWalV2rRootOwnedRoleCredentialAdmissionConfig
    public_values: tuple[tuple[str, object], ...]


_VERIFIED_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalV2rRootOwnedRoleCredentialAdmission, _AdmissionFacts
] = WeakKeyDictionary()


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_ROOT_REQUIRED")
    except OSError:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_ROOT_REQUIRED")


def _resolve_config(value: object) -> PhysicalWalV2rRootOwnedRoleCredentialAdmissionConfig:
    if (
        type(value) is not PhysicalWalV2rRootOwnedRoleCredentialAdmissionConfig
        or value.enabled is not True
        or type(value.admission_config)
        is not _mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig
        or type(value.provider_evidence_config)
        is not _provider.PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig
        or type(value.full_bundle_config)
        is not _bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig
        or value.provider_evidence_config.admission_config is not value.admission_config
    ):
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_CONFIG_INVALID")
    return value


def _fixed_path(*, local_role: object) -> Path:
    if type(local_role) is not str:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_ROLE_INVALID")
    filename = _ROLE_FILENAMES.get(local_role)
    policy = _POLICY_BY_ROLE.get(local_role)
    if filename is None or policy is None:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_ROLE_INVALID")
    root = PHYSICAL_WAL_V2R_ROOT_OWNED_ROLE_CREDENTIAL_ROOT
    if not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
    path = root / filename
    if path.parent != root or path.name != filename:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
    return path


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    del value
    _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")


def _credential_value(value: object) -> str:
    if (
        type(value) is not str
        or _VALUE_RE.fullmatch(value) is None
        or len(value.encode("ascii", "strict")) > _MAX_VALUE_BYTES
    ):
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
    return value


def _identity(access_key: str) -> str:
    return hashlib.sha256(_IDENTITY_DOMAIN + access_key.encode("ascii")).hexdigest()


def _read_exact_private_file(path: Path) -> tuple[bytes, int, int]:
    descriptor = -1
    try:
        parent_before, before = os.lstat(path.parent), os.lstat(path)
        resolved_parent = path.parent.resolve(strict=True)
        resolved = path.resolve(strict=True)
        if (
            resolved_parent != path.parent
            or resolved != path
            or stat.S_ISLNK(parent_before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(parent_before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or parent_before.st_uid != 0
            or before.st_uid != 0
            or stat.S_IMODE(parent_before.st_mode) != 0o700
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size < 2
            or before.st_size > _MAX_BYTES
            or not hasattr(os, "O_NOFOLLOW")
        ):
            _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
        descriptor = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size < 2
            or opened.st_size > _MAX_BYTES
        ):
            _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 4096)
            if type(chunk) is not bytes:
                _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_BYTES:
                _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
            chunks.append(chunk)
        after = os.lstat(path)
        if (
            total != opened.st_size
            or after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_uid != 0
            or after.st_nlink != 1
            or stat.S_ISLNK(after.st_mode)
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
        return b"".join(chunks), opened.st_dev, opened.st_ino
    except PhysicalWalV2rRootOwnedRoleCredentialAdmissionError:
        raise
    except OSError:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")


def _read_and_match_file(*, path: Path, admission: _mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission) -> tuple[int, int]:
    raw, device, inode = _read_exact_private_file(path)
    try:
        item = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except PhysicalWalV2rRootOwnedRoleCredentialAdmissionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
    if type(item) is not dict or set(item) != {
        "schema", "protocol_domain", "local_site", "local_role", "mailbox",
        "object_prefix", "access_key", "secret_key",
    }:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_FILE_INVALID")
    if (
        item["schema"] != _FILE_SCHEMA
        or item["protocol_domain"]
        != _v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN
        or item["local_site"] != admission.local_site
        or item["local_role"] != admission.local_role
        or item["mailbox"] != admission.mailbox
        or item["object_prefix"] != admission.object_prefix
    ):
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_SCOPE_INVALID")
    access_key = _credential_value(item["access_key"])
    _credential_value(item["secret_key"])
    if _identity(access_key) != admission.role_credential_identity_sha256:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_IDENTITY_MISMATCH")
    return device, inode


def _require_inputs(*, config: PhysicalWalV2rRootOwnedRoleCredentialAdmissionConfig, admission: object, provider_evidence: object, full_bundle: object, now: datetime) -> tuple[_mailbox.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission, _provider.VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidence, _bundle.VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle]:
    try:
        verified_admission = _mailbox.require_verified_physical_wal_v2r_witness_roundtrip_control_mailbox_admission(admission=admission, config=config.admission_config, now=now)
        verified_evidence = _provider.require_verified_physical_wal_v2r_provider_route_iam_object_lock_evidence(evidence=provider_evidence, config=config.provider_evidence_config, admission=verified_admission, now=now)
        verified_bundle = _bundle.require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle(full_bundle=full_bundle, config=config.full_bundle_config, now=now)
        _checked_bundle, roles = _bundle.require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle_site_manifest_slice(full_bundle=verified_bundle, site=verified_admission.local_site, now=now)
    except (_mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError, _provider.PhysicalWalV2rProviderRouteIamObjectLockEvidenceError, _bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError) as exc:
        raise PhysicalWalV2rRootOwnedRoleCredentialAdmissionError("V2R_ROOT_OWNED_ROLE_CREDENTIAL_PROOF_INVALID") from exc
    matching = [role for role in roles if role["local_role"] == verified_admission.local_role]
    if len(matching) != 1:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_BUNDLE_ROLE_INVALID")
    role = matching[0]
    exact = {
        "local_site": verified_admission.local_site,
        "local_role": verified_admission.local_role,
        "mailbox": verified_admission.mailbox,
        "direction": verified_admission.direction,
        "object_prefix": verified_admission.object_prefix,
        "role_credential_identity_sha256": verified_admission.role_credential_identity_sha256,
        "role_iam_policy_sha256": verified_admission.role_iam_policy_sha256,
        "provider_route_iam_attestation_sha256": verified_admission.provider_route_iam_attestation_sha256,
        "object_lock_retention_proof_sha256": verified_admission.object_lock_retention_proof_sha256,
    }
    if (
        any(role[name] != expected for name, expected in exact.items())
        or verified_evidence.local_role != verified_admission.local_role
        or verified_evidence.local_site != verified_admission.local_site
        or verified_evidence.mailbox != verified_admission.mailbox
        or verified_evidence.object_prefix != verified_admission.object_prefix
        or verified_evidence.role_credential_identity_sha256
        != verified_admission.role_credential_identity_sha256
    ):
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_CROSS_PIN_MISMATCH")
    return verified_admission, verified_evidence, verified_bundle


def admit_root_owned_physical_wal_v2r_witness_roundtrip_role_credential(
    *,
    config: PhysicalWalV2rRootOwnedRoleCredentialAdmissionConfig,
    admission: object,
    provider_evidence: object,
    full_bundle: object,
    now: datetime,
) -> VerifiedPhysicalWalV2rRootOwnedRoleCredentialAdmission:
    """Open and validate one fixed V2R file after all public proof checks.

    The return value intentionally excludes the access key and secret.  It is
    a non-operational boundary result, not permission to create a provider
    client or execute any carrier operation.
    """

    resolved = _resolve_config(config)
    verified_admission, verified_evidence, verified_bundle = _require_inputs(
        config=resolved,
        admission=admission,
        provider_evidence=provider_evidence,
        full_bundle=full_bundle,
        now=now,
    )
    _require_root()
    device, inode = _read_and_match_file(
        path=_fixed_path(local_role=verified_admission.local_role),
        admission=verified_admission,
    )
    values = (
        ("local_site", verified_admission.local_site),
        ("local_role", verified_admission.local_role),
        ("mailbox", verified_admission.mailbox),
        ("direction", verified_admission.direction),
        ("object_prefix", verified_admission.object_prefix),
        ("role_credential_identity_sha256", verified_admission.role_credential_identity_sha256),
        ("provider_evidence_sha256", verified_evidence.evidence_sha256),
        ("full_bundle_sha256", verified_bundle.full_bundle_sha256),
        ("credential_file_device", device),
        ("credential_file_inode", inode),
        ("is_operational", False),
        ("writer_authorized", False),
        ("promotion_authorized", False),
        ("traffic_authorized", False),
        ("phase5_authorized", False),
        ("execution_authorized", False),
        ("full_matrix_authorized", False),
        ("full_matrix_executed", False),
    )
    result = VerifiedPhysicalWalV2rRootOwnedRoleCredentialAdmission(
        capability=_CAPABILITY, **dict(values)
    )
    _VERIFIED_STATES[result] = _AdmissionFacts(config=resolved, public_values=values)
    return result


def require_verified_root_owned_physical_wal_v2r_witness_roundtrip_role_credential_admission(
    *,
    credential_admission: object,
    config: PhysicalWalV2rRootOwnedRoleCredentialAdmissionConfig,
) -> VerifiedPhysicalWalV2rRootOwnedRoleCredentialAdmission:
    """Check an opaque no-secret result without reopening the credential file."""

    resolved = _resolve_config(config)
    if (
        type(credential_admission)
        is not VerifiedPhysicalWalV2rRootOwnedRoleCredentialAdmission
        or credential_admission._capability is not _CAPABILITY
    ):
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_CAPABILITY_INVALID")
    facts = _VERIFIED_STATES.get(credential_admission)
    if facts is None or facts.config is not resolved:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_CAPABILITY_INVALID")
    if tuple((name, getattr(credential_admission, name)) for name, _ in facts.public_values) != facts.public_values:
        _fail("V2R_ROOT_OWNED_ROLE_CREDENTIAL_CAPABILITY_INVALID")
    return credential_admission
