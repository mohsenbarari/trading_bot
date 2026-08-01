"""Root-local durable anti-replay reservations for the V4 FI↔Witness lane.

This is a deliberately narrow, default-off persistence foundation.  It has no
transport, remote client, campaign runner, promotion control, or publication
code.  Its single public action permanently reserves a V4 request identifier
*before* a future role-local callback crosses an external boundary.

Each role has a separate fixed local namespace, fixed reservation prefix, and
fully pinned V4 policy identity.  Reservations are append-only, fsync'd, and
checked against a root-owned monotonic checkpoint.  A stale pointer, an
incomplete record sequence, a symlink, or any temporary/unknown residue fails
closed.  There is intentionally no release, retry, or delete operation: an
ambiguous callback outcome leaves its identifier burned.

The local append chain detects ordinary on-disk rollback.  Detecting a
privileged whole-tree restore also requires the injected root-owned monotonic
checkpoint; this module rejects use when that checkpoint is absent.  A remote
relay is not an authority for this state.  Production integration is still required;
each endpoint callback must call
``reserve_before_external_boundary`` and durably retain its receipt before it
publishes or consumes a matching item.  This module itself grants no execution
or promotion authority.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Protocol
from uuid import UUID

from core import physical_full_matrix_v4_witness_anchor_wire as wire


__all__ = (
    "FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_CONTROLLER_REPLAY_ID",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_READ_CHALLENGE",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WA_FI_CONTROLLER",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WITNESS",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayCheckpoint",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistryConfig",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationStore",
)


PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-fi-witness-anti-replay-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_DEFAULT_ENABLED = False

# Deployment owns this exact parent.  The two children are selected only from
# the closed role map below; callers never provide a filesystem path.
FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT = Path(
    "/var/lib/trading-bot/physical-full-matrix-v4-witness-anchor-fi-witness-anti-replay"
)

PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WA_FI_CONTROLLER = (
    "wa-fi-controller"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WITNESS = "witness"
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_READ_CHALLENGE = (
    "read-challenge"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_CONTROLLER_REPLAY_ID = (
    "controller-replay-id"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID = (
    "witness-observation-id"
)

_VERSION = 1
_MODE = "root-owned-v4-fi-witness-durable-anti-replay-reservation-v1"
_LOCK_FILENAME = "anti-replay.lock"
_BINDING_FILENAME = "binding.json"
_CURRENT_FILENAME = "current.json"
_RECORDS_DIRECTORY = "reservations"
_MAX_RECORD_BYTES = 64 * 1024
_MAX_RECORDS = 8_192
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RECORD_NAME_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.json$", re.ASCII)
_TEMP_NAME_RE = re.compile(r"^\.[A-Za-z0-9._-]+\.tmp$", re.ASCII)

_ROLE_SPECS: dict[str, tuple[str, str, frozenset[str]]] = {
    PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WA_FI_CONTROLLER: (
        "wa-fi-controller",
        "wa-fi-controller-v4-anchor-reservation",
        frozenset(
            {
                PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_READ_CHALLENGE,
                PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_CONTROLLER_REPLAY_ID,
                # The inbound response observation is recorded before the
                # controller returns its verified envelope to the adapter.
                PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID,
            }
        ),
    ),
    PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WITNESS: (
        "witness",
        "witness-v4-anchor-reservation",
        frozenset(
            {
                PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_READ_CHALLENGE,
                PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_CONTROLLER_REPLAY_ID,
                PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID,
            }
        ),
    ),
}


class PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(RuntimeError):
    """The root-local V4 reservation store rejected unsafe state."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(code)


class PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayCheckpoint(Protocol):
    """Root-owned monotonic checkpoint outside the mutable reservation tree.

    The implementation must durably remember the greatest accepted sequence
    and record hash for the complete binding.  It must allow an exact replay of
    its current value or its direct successor and reject a lower sequence or a
    same-sequence different hash.  A real deployment may bind this to a local
    root-owned monotonic facility, but this foundation intentionally provides
    neither a remote implementation nor a fallback.
    """

    def attest_v4_fi_witness_anti_replay_state(
        self,
        *,
        binding_sha256: str,
        role: str,
        state_namespace: str,
        reservation_prefix: str,
        sequence: int,
        previous_record_sha256: str,
        record_sha256: str,
    ) -> None: ...


class PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationStore(Protocol):
    """Future callback seam; callers must reserve before crossing a boundary."""

    def reserve_before_external_boundary(
        self,
        *,
        policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        identifier_kind: str,
        identifier: str,
    ) -> "PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt": ...


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistryConfig:
    """Pinned, default-off configuration with no caller-selected path."""

    schema: str = PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_DEFAULT_ENABLED
    role: str = ""
    policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity | None = None
    require_durable_rollback_checkpoint: bool = True


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt:
    """Evidence that one identifier is permanently unavailable for reuse."""

    schema: str
    role: str
    state_namespace: str
    reservation_prefix: str
    policy_identity_sha256: str
    identifier_kind: str
    identifier: str
    reservation_sequence: int
    reservation_record_sha256: str
    execution_authorized: bool = False
    promotion_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True)
class _Facts:
    role: str
    state_namespace: str
    reservation_prefix: str
    allowed_kinds: frozenset[str]
    identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity
    identity_payload: dict[str, object]
    identity_sha256: str
    binding_payload: bytes
    binding_sha256: str


@dataclass(frozen=True)
class _Record:
    sequence: int
    previous_record_sha256: str
    record_sha256: str
    identifier_kind: str
    identifier: str


@dataclass(frozen=True)
class _Storage:
    root_fd: int
    namespace_fd: int
    records_fd: int


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + b"\n"
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(code) from exc


def _strict_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _decode_canonical(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not bytes or not value or len(value) > _MAX_RECORD_BYTES:
        _fail(code)
    try:
        decoded = json.loads(
            value.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: _fail(code),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError,
    ):
        _fail(code)
    if type(decoded) is not dict or _canonical(decoded, code=code) != value:
        _fail(code)
    return decoded


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    if not permit_zero and value == _ZERO_SHA256:
        _fail(code)
    return value


def _positive(value: object, *, code: str, permit_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if permit_zero else 1) or value > 2**63 - 1:
        _fail(code)
    return value


def _identity_payload(
    identity: object,
    *,
    code: str,
) -> dict[str, object]:
    """Validate the exact V4 identity type without importing an adapter."""

    if type(identity) is not wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity:
        _fail(code)
    assert isinstance(identity, wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity)
    if (
        identity.schema != wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA
        or type(identity.run_id) is not UUID
    ):
        _fail(code)
    return {
        "schema": identity.schema,
        "journal_binding_sha256": _sha256(identity.journal_binding_sha256, code=code),
        "baseline_plan_binding_sha256": _sha256(
            identity.baseline_plan_binding_sha256,
            code=code,
        ),
        "run_id": str(identity.run_id),
        "plan_sha256": _sha256(identity.plan_sha256, code=code),
        "anchor_genesis_sequence": _positive(
            identity.anchor_genesis_sequence,
            code=code,
            permit_zero=True,
        ),
        "anchor_genesis_head_sha256": _sha256(
            identity.anchor_genesis_head_sha256,
            code=code,
        ),
        "canonical_genesis_sha256": _sha256(
            identity.canonical_genesis_sha256,
            code=code,
        ),
    }


def _facts(
    config: object,
) -> _Facts:
    if type(config) is not PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistryConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CONFIG_INVALID")
    assert isinstance(config, PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistryConfig)
    if config.schema != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CONFIG_INVALID")
    if config.enabled is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_DISABLED")
    if config.require_durable_rollback_checkpoint is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CHECKPOINT_REQUIRED")
    if type(config.role) is not str:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_ROLE_INVALID")
    spec = _ROLE_SPECS.get(config.role)
    if spec is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_ROLE_INVALID")
    state_namespace, reservation_prefix, allowed_kinds = spec
    identity_payload = _identity_payload(
        config.policy_identity,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_POLICY_IDENTITY_INVALID",
    )
    assert isinstance(config.policy_identity, wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity)
    identity_sha256 = hashlib.sha256(
        _canonical(
            identity_payload,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_POLICY_IDENTITY_INVALID",
        )
    ).hexdigest()
    body = {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "role": config.role,
        "state_namespace": state_namespace,
        "reservation_prefix": reservation_prefix,
        "policy_identity": identity_payload,
        "policy_identity_sha256": identity_sha256,
        "execution_authorized": False,
        "promotion_authorized": False,
        "full_matrix_executed": False,
    }
    binding_payload = _canonical(
        body,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_BINDING_INVALID",
    )
    return _Facts(
        role=config.role,
        state_namespace=state_namespace,
        reservation_prefix=reservation_prefix,
        allowed_kinds=allowed_kinds,
        identity=config.policy_identity,
        identity_payload=identity_payload,
        identity_sha256=identity_sha256,
        binding_payload=binding_payload,
        binding_sha256=hashlib.sha256(binding_payload).hexdigest(),
    )


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _require_fd_platform() -> None:
    if not all(hasattr(os, item) for item in ("O_NOFOLLOW", "O_DIRECTORY", "fdatasync")):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_PLATFORM_UNSUPPORTED")


def _metadata_tuple(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
    )


def _validate_ancestors(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT_UNSAFE")
    _require_fd_platform()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT_UNSAFE")
    except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT_UNSAFE"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_secure_root() -> int:
    root = FIXED_PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT
    _validate_ancestors(root)
    descriptor = -1
    try:
        before = os.lstat(root)
        resolved = root.resolve(strict=True)
        if (
            resolved != root
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) != 0o700
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT_UNSAFE")
        descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        after = os.lstat(root)
        if (
            _metadata_tuple(before) != _metadata_tuple(opened)
            or _metadata_tuple(after) != _metadata_tuple(before)
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT_UNSAFE")
        return descriptor
    except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_ROOT_UNSAFE"
        ) from exc


def _safe_child_metadata(
    parent_fd: int,
    name: str,
    *,
    directory: bool,
    code: str,
) -> os.stat_result:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or (not stat.S_ISDIR(metadata.st_mode) if directory else not stat.S_ISREG(metadata.st_mode))
        or (directory and metadata.st_nlink < 2)
        or (not directory and metadata.st_nlink != 1)
        or stat.S_IMODE(metadata.st_mode) != (0o700 if directory else 0o600)
    ):
        _fail(code)
    return metadata


def _listdir(parent_fd: int, *, code: str) -> list[str]:
    try:
        names = os.listdir(parent_fd)
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(code) from exc
    if any(type(name) is not str or not name or "/" in name or "\\" in name for name in names):
        _fail(code)
    return names


def _ensure_namespace(root_fd: int, *, facts: _Facts) -> int:
    descriptor = -1
    try:
        allowed_namespaces = {spec[0] for spec in _ROLE_SPECS.values()}
        for name in _listdir(
            root_fd,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_ROOT_RESIDUE",
        ):
            if name not in allowed_namespaces:
                _fail(
                    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_ROOT_TEMP_RESIDUE"
                    if _TEMP_NAME_RE.fullmatch(name)
                    else "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_ROOT_RESIDUE"
                )
            _safe_child_metadata(
                root_fd,
                name,
                directory=True,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_NAMESPACE_UNSAFE",
            )
        created = False
        try:
            os.mkdir(facts.state_namespace, 0o700, dir_fd=root_fd)
            created = True
        except FileExistsError:
            pass
        descriptor = os.open(
            facts.state_namespace,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_fd,
        )
        if created:
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
            os.fsync(root_fd)
        before = _safe_child_metadata(
            root_fd,
            facts.state_namespace,
            directory=True,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_NAMESPACE_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            root_fd,
            facts.state_namespace,
            directory=True,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_NAMESPACE_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_NAMESPACE_UNSAFE")
        return descriptor
    except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_NAMESPACE_UNSAFE"
        ) from exc


def _ensure_records_directory(namespace_fd: int) -> int:
    descriptor = -1
    try:
        created = False
        try:
            os.mkdir(_RECORDS_DIRECTORY, 0o700, dir_fd=namespace_fd)
            created = True
        except FileExistsError:
            pass
        descriptor = os.open(
            _RECORDS_DIRECTORY,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=namespace_fd,
        )
        if created:
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
            os.fsync(namespace_fd)
        before = _safe_child_metadata(
            namespace_fd,
            _RECORDS_DIRECTORY,
            directory=True,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORDS_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            namespace_fd,
            _RECORDS_DIRECTORY,
            directory=True,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORDS_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORDS_UNSAFE")
        return descriptor
    except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORDS_UNSAFE"
        ) from exc


def _open_lock(namespace_fd: int) -> int:
    descriptor = -1
    try:
        created = False
        try:
            descriptor = os.open(
                _LOCK_FILENAME,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=namespace_fd,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(
                _LOCK_FILENAME,
                os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=namespace_fd,
            )
        if created:
            os.fchmod(descriptor, 0o600)
            os.fdatasync(descriptor)
            os.fsync(namespace_fd)
        before = _safe_child_metadata(
            namespace_fd,
            _LOCK_FILENAME,
            directory=False,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_LOCK_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            namespace_fd,
            _LOCK_FILENAME,
            directory=False,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_LOCK_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_LOCK_OPEN_FAILED"
        ) from exc


def _validate_namespace_entries(namespace_fd: int) -> None:
    known = {_LOCK_FILENAME, _BINDING_FILENAME, _CURRENT_FILENAME, _RECORDS_DIRECTORY}
    for name in _listdir(
        namespace_fd,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_NAMESPACE_RESIDUE",
    ):
        if name not in known:
            _fail(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_TEMP_RESIDUE"
                if _TEMP_NAME_RE.fullmatch(name)
                else "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_NAMESPACE_RESIDUE"
            )
        _safe_child_metadata(
            namespace_fd,
            name,
            directory=name == _RECORDS_DIRECTORY,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_NAMESPACE_UNSAFE",
        )


@contextmanager
def _locked_storage(*, facts: _Facts) -> Iterator[_Storage]:
    root_fd = -1
    namespace_fd = -1
    records_fd = -1
    lock_fd = -1
    try:
        root_fd = _open_secure_root()
        namespace_fd = _ensure_namespace(root_fd, facts=facts)
        records_fd = _ensure_records_directory(namespace_fd)
        lock_fd = _open_lock(namespace_fd)
        _validate_namespace_entries(namespace_fd)
        yield _Storage(root_fd=root_fd, namespace_fd=namespace_fd, records_fd=records_fd)
    finally:
        try:
            if lock_fd >= 0:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            for descriptor in (lock_fd, records_fd, namespace_fd, root_fd):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass


def _read_file_at(parent_fd: int, name: str, *, code: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        before = _safe_child_metadata(parent_fd, name, directory=False, code=code)
        metadata = os.fstat(descriptor)
        after = _safe_child_metadata(parent_fd, name, directory=False, code=code)
        if (
            _metadata_tuple(before) != _metadata_tuple(metadata)
            or _metadata_tuple(after) != _metadata_tuple(before)
            or not 1 <= metadata.st_size <= _MAX_RECORD_BYTES
        ):
            _fail(code)
        remaining = metadata.st_size
        chunks = bytearray()
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail(code)
            chunks.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(chunks)
    except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_all(descriptor: int, payload: bytes, *, code: str) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError as exc:
            raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(code) from exc
        if type(written) is not int or written <= 0:
            _fail(code)
        view = view[written:]


def _write_create_only_at(parent_fd: int, name: str, payload: bytes, *, code: str) -> None:
    if (
        type(name) is not str
        or not name
        or "/" in name
        or "\\" in name
        or name in {".", ".."}
        or type(payload) is not bytes
        or not 1 <= len(payload) <= _MAX_RECORD_BYTES
    ):
        _fail(code)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
        except FileExistsError:
            if _read_file_at(parent_fd, name, code=code) != payload:
                _fail(code)
            return
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_STATE_UNSAFE")
        _write_all(descriptor, payload, code=code)
        os.fdatasync(descriptor)
    except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_DIRECTORY_FSYNC_FAILED"
        ) from exc


def _write_current_atomic(namespace_fd: int, payload: bytes) -> None:
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_RECORD_BYTES:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_INVALID")
    temporary = ".current-" + secrets.token_bytes(32).hex() + ".tmp"
    descriptor = -1
    try:
        try:
            _safe_child_metadata(
                namespace_fd,
                _CURRENT_FILENAME,
                directory=False,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_UNSAFE",
            )
        except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError as exc:
            if not isinstance(exc.__cause__, FileNotFoundError):
                try:
                    os.stat(_CURRENT_FILENAME, dir_fd=namespace_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                except OSError as inner:
                    raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(
                        "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_UNSAFE"
                    ) from inner
                else:
                    raise exc
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=namespace_fd,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_UNSAFE")
        _write_all(
            descriptor,
            payload,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_WRITE_FAILED",
        )
        os.fdatasync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary,
            _CURRENT_FILENAME,
            src_dir_fd=namespace_fd,
            dst_dir_fd=namespace_fd,
        )
        _safe_child_metadata(
            namespace_fd,
            _CURRENT_FILENAME,
            directory=False,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_UNSAFE",
        )
        os.fsync(namespace_fd)
    except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_WRITE_FAILED"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _binding_payload(facts: _Facts) -> bytes:
    return facts.binding_payload


def _record_payload(
    *,
    facts: _Facts,
    sequence: int,
    previous_record_sha256: str,
    identifier_kind: str,
    identifier: str,
) -> tuple[bytes, str]:
    body = {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "role": facts.role,
        "state_namespace": facts.state_namespace,
        "reservation_prefix": facts.reservation_prefix,
        "policy_identity_sha256": facts.identity_sha256,
        "sequence": sequence,
        "previous_record_sha256": previous_record_sha256,
        "identifier_kind": identifier_kind,
        "identifier": identifier,
    }
    digest = hashlib.sha256(
        _canonical(
            body,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORD_INVALID",
        )
    ).hexdigest()
    return (
        _canonical(
            {**body, "record_sha256": digest},
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORD_INVALID",
        ),
        digest,
    )


def _record_from_payload(
    payload: bytes,
    *,
    facts: _Facts,
    expected_sequence: int,
    expected_previous_record_sha256: str,
) -> _Record:
    code = "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORD_INVALID"
    decoded = _decode_canonical(payload, code=code)
    required = {
        "schema",
        "version",
        "mode",
        "role",
        "state_namespace",
        "reservation_prefix",
        "policy_identity_sha256",
        "sequence",
        "previous_record_sha256",
        "identifier_kind",
        "identifier",
        "record_sha256",
    }
    if set(decoded) != required:
        _fail(code)
    sequence = _positive(decoded["sequence"], code=code)
    previous = _sha256(decoded["previous_record_sha256"], code=code, permit_zero=True)
    identifier_kind = decoded["identifier_kind"]
    if type(identifier_kind) is not str or identifier_kind not in facts.allowed_kinds:
        _fail(code)
    identifier = _sha256(decoded["identifier"], code=code)
    record_sha256 = _sha256(decoded["record_sha256"], code=code)
    if (
        decoded["schema"] != PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA
        or decoded["version"] != _VERSION
        or decoded["mode"] != _MODE
        or decoded["role"] != facts.role
        or decoded["state_namespace"] != facts.state_namespace
        or decoded["reservation_prefix"] != facts.reservation_prefix
        or decoded["policy_identity_sha256"] != facts.identity_sha256
        or sequence != expected_sequence
        or previous != expected_previous_record_sha256
    ):
        _fail(code)
    expected_payload, expected_digest = _record_payload(
        facts=facts,
        sequence=sequence,
        previous_record_sha256=previous,
        identifier_kind=identifier_kind,
        identifier=identifier,
    )
    if payload != expected_payload or record_sha256 != expected_digest:
        _fail(code)
    return _Record(
        sequence=sequence,
        previous_record_sha256=previous,
        record_sha256=record_sha256,
        identifier_kind=identifier_kind,
        identifier=identifier,
    )


def _current_payload(*, facts: _Facts, record: _Record) -> bytes:
    return _canonical(
        {
            "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA,
            "version": _VERSION,
            "mode": _MODE,
            "role": facts.role,
            "state_namespace": facts.state_namespace,
            "reservation_prefix": facts.reservation_prefix,
            "policy_identity_sha256": facts.identity_sha256,
            "sequence": record.sequence,
            "record_sha256": record.record_sha256,
            "identifier_kind": record.identifier_kind,
            "identifier": record.identifier,
        },
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_INVALID",
    )


def _load_state(storage: _Storage, *, facts: _Facts) -> tuple[_Record, ...]:
    binding_payload = _binding_payload(facts)
    _write_create_only_at(
        storage.namespace_fd,
        _BINDING_FILENAME,
        binding_payload,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_BINDING_MISMATCH",
    )
    records: list[tuple[int, str, str]] = []
    for name in _listdir(
        storage.records_fd,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORDS_RESIDUE",
    ):
        match = _RECORD_NAME_RE.fullmatch(name)
        if match is None:
            _fail(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_TEMP_RESIDUE"
                if _TEMP_NAME_RE.fullmatch(name)
                else "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORDS_RESIDUE"
            )
        _safe_child_metadata(
            storage.records_fd,
            name,
            directory=False,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORD_UNSAFE",
        )
        records.append((int(match.group(1)), match.group(2), name))
    if len(records) > _MAX_RECORDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORD_LIMIT")
    records.sort()
    expected_sequence = 1
    previous = _ZERO_SHA256
    result: list[_Record] = []
    seen_identifiers: set[str] = set()
    for sequence, file_identifier, name in records:
        if sequence != expected_sequence:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORD_ROLLBACK")
        record = _record_from_payload(
            _read_file_at(
                storage.records_fd,
                name,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORD_INVALID",
            ),
            facts=facts,
            expected_sequence=expected_sequence,
            expected_previous_record_sha256=previous,
        )
        if record.identifier != file_identifier or record.identifier in seen_identifiers:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORD_INVALID")
        seen_identifiers.add(record.identifier)
        result.append(record)
        previous = record.record_sha256
        expected_sequence += 1
    try:
        current = _read_file_at(
            storage.namespace_fd,
            _CURRENT_FILENAME,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_INVALID",
        )
    except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            if result:
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_ROLLBACK")
            return tuple()
        raise
    if not result or current != _current_payload(facts=facts, record=result[-1]):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CURRENT_ROLLBACK")
    return tuple(result)


def _checkpoint(
    checkpoint: object,
    *,
    facts: _Facts,
    record: _Record | None,
) -> None:
    callback = getattr(checkpoint, "attest_v4_fi_witness_anti_replay_state", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CHECKPOINT_MISSING")
    try:
        result = callback(
            binding_sha256=facts.binding_sha256,
            role=facts.role,
            state_namespace=facts.state_namespace,
            reservation_prefix=facts.reservation_prefix,
            sequence=0 if record is None else record.sequence,
            previous_record_sha256=(
                _ZERO_SHA256 if record is None else record.previous_record_sha256
            ),
            record_sha256=_ZERO_SHA256 if record is None else record.record_sha256,
        )
    except PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CHECKPOINT_REJECTED"
        ) from exc
    if result is not None:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CHECKPOINT_INVALID")


def _require_checkpoint_callback(checkpoint: object) -> None:
    if not callable(getattr(checkpoint, "attest_v4_fi_witness_anti_replay_state", None)):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_CHECKPOINT_MISSING")


class PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistry:
    """Root-gated append-only reservation implementation.

    A returned receipt only states that the identifier has been burned in this
    role's durable namespace.  It never authorizes a callback, campaign phase,
    writer, or traffic transition.
    """

    def __init__(
        self,
        config: PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayRegistryConfig,
        *,
        rollback_checkpoint: PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayCheckpoint
        | None,
    ) -> None:
        self._config = config
        self._rollback_checkpoint = rollback_checkpoint

    def reserve_before_external_boundary(
        self,
        *,
        policy_identity: wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        identifier_kind: str,
        identifier: str,
    ) -> PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt:
        """Permanently reserve one V4 identifier before a future callback.

        The record and its monotonic checkpoint are durable before this method
        returns.  Any later callback failure must retain the receipt and must
        not retry the same identifier.
        """

        facts = _facts(self._config)
        _require_root()
        _require_checkpoint_callback(self._rollback_checkpoint)
        supplied_identity = _identity_payload(
            policy_identity,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_POLICY_IDENTITY_MISMATCH",
        )
        if supplied_identity != facts.identity_payload:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_POLICY_IDENTITY_MISMATCH")
        if type(identifier_kind) is not str or identifier_kind not in facts.allowed_kinds:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_IDENTIFIER_KIND_INVALID")
        identifier = _sha256(
            identifier,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_IDENTIFIER_INVALID",
        )
        with _locked_storage(facts=facts) as storage:
            records = _load_state(storage, facts=facts)
            previous = records[-1] if records else None
            _checkpoint(self._rollback_checkpoint, facts=facts, record=previous)
            if any(record.identifier == identifier for record in records):
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_IDENTIFIER_REUSED")
            sequence = 1 if previous is None else previous.sequence + 1
            previous_sha256 = _ZERO_SHA256 if previous is None else previous.record_sha256
            payload, record_sha256 = _record_payload(
                facts=facts,
                sequence=sequence,
                previous_record_sha256=previous_sha256,
                identifier_kind=identifier_kind,
                identifier=identifier,
            )
            _write_create_only_at(
                storage.records_fd,
                f"{sequence:020d}-{identifier}.json",
                payload,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_RECORD_WRITE_FAILED",
            )
            record = _Record(
                sequence=sequence,
                previous_record_sha256=previous_sha256,
                record_sha256=record_sha256,
                identifier_kind=identifier_kind,
                identifier=identifier,
            )
            _write_current_atomic(
                storage.namespace_fd,
                _current_payload(facts=facts, record=record),
            )
            _checkpoint(self._rollback_checkpoint, facts=facts, record=record)
            return PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt(
                schema=PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA,
                role=facts.role,
                state_namespace=facts.state_namespace,
                reservation_prefix=facts.reservation_prefix,
                policy_identity_sha256=facts.identity_sha256,
                identifier_kind=identifier_kind,
                identifier=identifier,
                reservation_sequence=sequence,
                reservation_record_sha256=record_sha256,
            )
