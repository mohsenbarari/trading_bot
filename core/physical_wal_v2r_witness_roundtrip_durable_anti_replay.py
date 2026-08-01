"""Root-local durable anti-replay reservations for the isolated V2R carrier.

V2R is the reverse four-hop evidence carrier
``WA-IR -> Witness -> WA-FI -> Witness -> WA-IR``.  This module is a narrow,
default-off, receiver-local persistence foundation for its four *receiving*
roles only.  It has no transport, provider client, database, peer link,
election, lease, promotion, writer, traffic, or recovery implementation.

Before a future adapter accepts one V2R correlation at a receiving boundary,
it must obtain a reservation here and durably retain the returned receipt.
Reservations are append-only and fsync'd.  A stale pointer, incomplete chain,
symlink, temporary residue, configuration switch, or identifier reuse fails
closed.  A role never receives a caller-selected path: each of the four
contractual receiver roles maps to one fixed namespace and reservation prefix.

Ordinary local rollback is detected by the append chain.  Detecting a
privileged whole-tree restore additionally requires the injected root-owned
monotonic checkpoint outside this mutable tree; there is deliberately no
fallback checkpoint.  Production integration remains required for the
checkpoint, Object Storage callback plumbing, and a durable adapter for
``PhysicalWalV2rWitnessRoundtripReplayGuard``.  This module itself grants no
authority.
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

from core import physical_wal_v2r_witness_roundtrip_contract as v2r


__all__ = (
    "FIXED_PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_STATE_ROOT",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_SCHEMA",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WITNESS_REVERSE_INGRESS",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WA_FI_RECOVERY_INBOX",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WITNESS_FI_ACK_INGRESS",
    "PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WA_IR_RETURN_INBOX",
    "PhysicalWalV2rWitnessRoundtripDurableAntiReplayCheckpoint",
    "PhysicalWalV2rWitnessRoundtripDurableAntiReplayError",
    "PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry",
    "PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistryConfig",
    "PhysicalWalV2rWitnessRoundtripDurableAntiReplayReservationReceipt",
    "PhysicalWalV2rWitnessRoundtripDurableAntiReplayReservationStore",
)


PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_SCHEMA = (
    "gold-trade-physical-wal-v2r-witness-roundtrip-durable-anti-replay-v1"
)
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_DEFAULT_ENABLED = False

# Deployment owns this parent.  Children are selected only from _ROLE_SPECS;
# no registry caller can supply a directory, prefix, site, or receiver role.
FIXED_PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_STATE_ROOT = Path(
    "/var/lib/trading-bot/physical-wal-v2r-witness-roundtrip-anti-replay"
)

PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WITNESS_REVERSE_INGRESS = (
    "witness-v2r-reverse-ingress"
)
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WA_FI_RECOVERY_INBOX = (
    "wa-fi-v2r-recovery-inbox"
)
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WITNESS_FI_ACK_INGRESS = (
    "witness-v2r-ack-ingress"
)
PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WA_IR_RETURN_INBOX = (
    "wa-ir-v2r-return-inbox"
)

_VERSION = 1
_MODE = "root-owned-v2r-four-hop-durable-anti-replay-reservation-v1"
_LOCK_FILENAME = "anti-replay.lock"
_BINDING_FILENAME = "binding.json"
_ROOT_CONFIGURATION_FILENAME = "v2r-configuration-binding.json"
_CURRENT_FILENAME = "current.json"
_RECORDS_DIRECTORY = "reservations"
_MAX_RECORD_BYTES = 64 * 1024
_MAX_RECORDS = 8_192
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_RECORD_NAME_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.json$", re.ASCII)
_TEMP_NAME_RE = re.compile(r"^\.[A-Za-z0-9._-]+\.tmp$", re.ASCII)

# (local site, contractual local role, receiving stage, namespace, prefix).
# A V2R correlation deliberately survives all four hops, so replay scope is
# one contractual receiver.  The same correlation may appear once in each
# distinct fixed receiver namespace; it can never be substituted across their
# role/stage-bound records or reused within one receiver.
_ROLE_SPECS: dict[str, tuple[str, str, str, str, str]] = {
    PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WITNESS_REVERSE_INGRESS: (
        "witness",
        "witness-v2r-reverse-ingress",
        "export",
        "witness-reverse-ingress",
        "v2r-witness-reverse-ingress-correlation-reservation",
    ),
    PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WA_FI_RECOVERY_INBOX: (
        "wa-fi",
        "wa-fi-v2r-recovery-inbox",
        "forward",
        "wa-fi-recovery-inbox",
        "v2r-wa-fi-recovery-inbox-correlation-reservation",
    ),
    PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WITNESS_FI_ACK_INGRESS: (
        "witness",
        "witness-v2r-ack-ingress",
        "ack",
        "witness-fi-ack-ingress",
        "v2r-witness-fi-ack-ingress-correlation-reservation",
    ),
    PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_ROLE_WA_IR_RETURN_INBOX: (
        "wa-ir",
        "wa-ir-v2r-return-inbox",
        "return",
        "wa-ir-return-inbox",
        "v2r-wa-ir-return-inbox-correlation-reservation",
    ),
}


class PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(RuntimeError):
    """The root-local V2R reservation state was absent, foreign, or unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(code)


class PhysicalWalV2rWitnessRoundtripDurableAntiReplayCheckpoint(Protocol):
    """Root-owned monotonic state outside the mutable reservation tree.

    The implementation must accept only an exact replay of its recorded head
    or its immediate successor for this complete binding.  It must reject a
    lower sequence, a branch, or a same-sequence different record hash.  This
    module intentionally supplies neither a remote checkpoint nor a fallback.
    """

    def attest_v2r_roundtrip_anti_replay_state(
        self,
        *,
        binding_sha256: str,
        receiving_role: str,
        state_namespace: str,
        reservation_prefix: str,
        sequence: int,
        previous_record_sha256: str,
        record_sha256: str,
    ) -> None: ...


class PhysicalWalV2rWitnessRoundtripDurableAntiReplayReservationStore(Protocol):
    """Future receiver adapter seam; reserve before accepting one correlation."""

    def reserve_before_receive(
        self,
        *,
        roundtrip_config: v2r.PhysicalWalV2rWitnessRoundtripConfig,
        stage: str,
        correlation_id: str,
    ) -> "PhysicalWalV2rWitnessRoundtripDurableAntiReplayReservationReceipt": ...


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistryConfig:
    """Pinned, default-off configuration with no caller-selected state path."""

    schema: str = PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_SCHEMA
    enabled: bool = PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_DEFAULT_ENABLED
    receiving_role: str = ""
    roundtrip_config: v2r.PhysicalWalV2rWitnessRoundtripConfig | None = None
    require_durable_rollback_checkpoint: bool = True


@dataclass(frozen=True)
class PhysicalWalV2rWitnessRoundtripDurableAntiReplayReservationReceipt:
    """Evidence that one receiver-local V2R correlation has been burned.

    It deliberately carries only non-authority facts.  A receipt cannot grant
    election, lease, writer, traffic, promotion, execution, or storage power.
    """

    schema: str
    receiving_role: str
    local_site: str
    local_role: str
    stage: str
    state_namespace: str
    reservation_prefix: str
    v2r_configuration_sha256: str
    v2r_full_configuration_sha256: str
    correlation_id_sha256: str
    reservation_sequence: int
    reservation_record_sha256: str
    object_storage_election_authority: bool = False
    object_storage_lease_authority: bool = False
    object_storage_writer_authority: bool = False
    writer_authorized: bool = False
    traffic_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False


@dataclass(frozen=True)
class _Facts:
    receiving_role: str
    local_site: str
    local_role: str
    stage: str
    state_namespace: str
    reservation_prefix: str
    v2r_configuration_sha256: str
    v2r_full_configuration_payload: dict[str, object]
    v2r_full_configuration_sha256: str
    binding_payload: bytes
    binding_sha256: str


@dataclass(frozen=True)
class _Record:
    sequence: int
    previous_record_sha256: str
    record_sha256: str
    correlation_id_sha256: str


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
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(code) from exc


def _strict_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            _fail("V2R_DURABLE_ANTI_REPLAY_DUPLICATE_JSON_KEY")
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
        PhysicalWalV2rWitnessRoundtripDurableAntiReplayError,
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


def _validated_v2r_configuration_payload(
    config: object,
    *,
    code: str,
) -> tuple[dict[str, object], str, str]:
    """Bind every current V2R config field, plus its wire identity digest.

    Calling the V2R contract's own validator preserves the exact same
    admission conditions as a receiver.  The full payload additionally pins
    timing parameters, enabled state, and every current dataclass field, so a
    future V2R config shape change fails closed rather than silently weakening
    this local binding.
    """

    if type(config) is not v2r.PhysicalWalV2rWitnessRoundtripConfig:
        _fail(code)
    assert isinstance(config, v2r.PhysicalWalV2rWitnessRoundtripConfig)
    expected_fields = {
        "cluster_id",
        "release_sha",
        "stream_generation_id",
        "route_commitment_sha256",
        "reverse_frontier_sha256",
        "recovery_frontier_sha256",
        "blob_frontier_sha256",
        "v2r_iam_policy_sha256",
        "normal_v2_protocol_domain",
        "normal_v2_mailbox_prefix",
        "normal_v2_iam_policy_sha256",
        "normal_v2_public_key_sha256s",
        "ir_export_public_key",
        "witness_forward_public_key",
        "fi_ack_public_key",
        "witness_return_public_key",
        "v2r_mailbox_prefix",
        "enabled",
        "maximum_evidence_age_seconds",
        "maximum_future_skew_seconds",
    }
    if set(config.__dataclass_fields__) != expected_fields:
        _fail(code)
    try:
        checked = v2r._validated_config(config)
    except Exception as exc:
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(code) from exc
    wire_digest = _sha256(checked.configuration_sha256, code=code)
    key_hashes = {
        field: hashlib.sha256(getattr(config, field)).hexdigest()
        for field in (
            "ir_export_public_key",
            "witness_forward_public_key",
            "fi_ack_public_key",
            "witness_return_public_key",
        )
    }
    payload: dict[str, object] = {
        "protocol_domain": v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN,
        "wire_configuration_sha256": wire_digest,
        "cluster_id": config.cluster_id,
        "release_sha": config.release_sha,
        "stream_generation_id": config.stream_generation_id,
        "route_commitment_sha256": config.route_commitment_sha256,
        "reverse_frontier_sha256": config.reverse_frontier_sha256,
        "recovery_frontier_sha256": config.recovery_frontier_sha256,
        "blob_frontier_sha256": config.blob_frontier_sha256,
        "v2r_iam_policy_sha256": config.v2r_iam_policy_sha256,
        "normal_v2_protocol_domain": config.normal_v2_protocol_domain,
        "normal_v2_mailbox_prefix": config.normal_v2_mailbox_prefix,
        "normal_v2_iam_policy_sha256": config.normal_v2_iam_policy_sha256,
        "normal_v2_public_key_sha256s": list(config.normal_v2_public_key_sha256s),
        "v2r_signer_public_key_sha256s": key_hashes,
        "v2r_mailbox_prefix": config.v2r_mailbox_prefix,
        "enabled": config.enabled,
        "maximum_evidence_age_seconds": config.maximum_evidence_age_seconds,
        "maximum_future_skew_seconds": config.maximum_future_skew_seconds,
    }
    digest = hashlib.sha256(_canonical(payload, code=code)).hexdigest()
    return payload, wire_digest, digest


def _facts(config: object) -> _Facts:
    if type(config) is not PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistryConfig:
        _fail("V2R_DURABLE_ANTI_REPLAY_CONFIG_INVALID")
    assert isinstance(config, PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistryConfig)
    if config.schema != PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_SCHEMA:
        _fail("V2R_DURABLE_ANTI_REPLAY_CONFIG_INVALID")
    if config.enabled is not True:
        _fail("V2R_DURABLE_ANTI_REPLAY_DISABLED")
    if config.require_durable_rollback_checkpoint is not True:
        _fail("V2R_DURABLE_ANTI_REPLAY_CHECKPOINT_REQUIRED")
    if type(config.receiving_role) is not str:
        _fail("V2R_DURABLE_ANTI_REPLAY_RECEIVING_ROLE_INVALID")
    role_spec = _ROLE_SPECS.get(config.receiving_role)
    if role_spec is None:
        _fail("V2R_DURABLE_ANTI_REPLAY_RECEIVING_ROLE_INVALID")
    local_site, local_role, stage, state_namespace, reservation_prefix = role_spec
    identity_payload, wire_digest, full_digest = _validated_v2r_configuration_payload(
        config.roundtrip_config,
        code="V2R_DURABLE_ANTI_REPLAY_V2R_CONFIGURATION_INVALID",
    )
    body = {
        "schema": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "receiving_role": config.receiving_role,
        "local_site": local_site,
        "local_role": local_role,
        "stage": stage,
        "state_namespace": state_namespace,
        "reservation_prefix": reservation_prefix,
        "v2r_configuration": identity_payload,
        "v2r_configuration_sha256": wire_digest,
        "v2r_full_configuration_sha256": full_digest,
        "object_storage_election_authority": False,
        "object_storage_lease_authority": False,
        "object_storage_writer_authority": False,
        "writer_authorized": False,
        "traffic_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }
    binding_payload = _canonical(body, code="V2R_DURABLE_ANTI_REPLAY_BINDING_INVALID")
    return _Facts(
        receiving_role=config.receiving_role,
        local_site=local_site,
        local_role=local_role,
        stage=stage,
        state_namespace=state_namespace,
        reservation_prefix=reservation_prefix,
        v2r_configuration_sha256=wire_digest,
        v2r_full_configuration_payload=identity_payload,
        v2r_full_configuration_sha256=full_digest,
        binding_payload=binding_payload,
        binding_sha256=hashlib.sha256(binding_payload).hexdigest(),
    )


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("V2R_DURABLE_ANTI_REPLAY_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(
            "V2R_DURABLE_ANTI_REPLAY_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _require_fd_platform() -> None:
    if not all(hasattr(os, item) for item in ("O_NOFOLLOW", "O_DIRECTORY", "fdatasync")):
        _fail("V2R_DURABLE_ANTI_REPLAY_PLATFORM_UNSUPPORTED")


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
        _fail("V2R_DURABLE_ANTI_REPLAY_STATE_ROOT_UNSAFE")
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
                _fail("V2R_DURABLE_ANTI_REPLAY_STATE_ROOT_UNSAFE")
    except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError:
        raise
    except OSError as exc:
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(
            "V2R_DURABLE_ANTI_REPLAY_STATE_ROOT_UNSAFE"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_secure_root() -> int:
    root = FIXED_PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_STATE_ROOT
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
            _fail("V2R_DURABLE_ANTI_REPLAY_STATE_ROOT_UNSAFE")
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
            _fail("V2R_DURABLE_ANTI_REPLAY_STATE_ROOT_UNSAFE")
        return descriptor
    except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(
            "V2R_DURABLE_ANTI_REPLAY_STATE_ROOT_UNSAFE"
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
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(code) from exc
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
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(code) from exc
    if any(type(name) is not str or not name or "/" in name or "\\" in name for name in names):
        _fail(code)
    return names


def _write_all(descriptor: int, payload: bytes, *, code: str) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError as exc:
            raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(code) from exc
        if type(written) is not int or written <= 0:
            _fail(code)
        view = view[written:]


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
    except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError:
        raise
    except OSError as exc:
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


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
            _fail("V2R_DURABLE_ANTI_REPLAY_STATE_UNSAFE")
        _write_all(descriptor, payload, code=code)
        os.fdatasync(descriptor)
    except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError:
        raise
    except OSError as exc:
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(
            "V2R_DURABLE_ANTI_REPLAY_DIRECTORY_FSYNC_FAILED"
        ) from exc


def _validate_root_entries(root_fd: int) -> None:
    allowed_namespaces = {spec[3] for spec in _ROLE_SPECS.values()}
    for name in _listdir(root_fd, code="V2R_DURABLE_ANTI_REPLAY_ROOT_RESIDUE"):
        if name == _ROOT_CONFIGURATION_FILENAME:
            _safe_child_metadata(
                root_fd,
                name,
                directory=False,
                code="V2R_DURABLE_ANTI_REPLAY_ROOT_BINDING_UNSAFE",
            )
            continue
        if name not in allowed_namespaces:
            _fail(
                "V2R_DURABLE_ANTI_REPLAY_ROOT_TEMP_RESIDUE"
                if _TEMP_NAME_RE.fullmatch(name)
                else "V2R_DURABLE_ANTI_REPLAY_ROOT_RESIDUE"
            )
        _safe_child_metadata(
            root_fd,
            name,
            directory=True,
            code="V2R_DURABLE_ANTI_REPLAY_NAMESPACE_UNSAFE",
        )


def _ensure_root_configuration_binding(root_fd: int, *, facts: _Facts) -> None:
    payload = _canonical(
        {
            "schema": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_SCHEMA,
            "version": _VERSION,
            "mode": _MODE,
            "v2r_configuration": facts.v2r_full_configuration_payload,
            "v2r_configuration_sha256": facts.v2r_configuration_sha256,
            "v2r_full_configuration_sha256": facts.v2r_full_configuration_sha256,
            "object_storage_election_authority": False,
            "object_storage_lease_authority": False,
            "object_storage_writer_authority": False,
            "writer_authorized": False,
            "traffic_authorized": False,
            "promotion_authorized": False,
            "execution_authorized": False,
        },
        code="V2R_DURABLE_ANTI_REPLAY_ROOT_BINDING_INVALID",
    )
    _write_create_only_at(
        root_fd,
        _ROOT_CONFIGURATION_FILENAME,
        payload,
        code="V2R_DURABLE_ANTI_REPLAY_ROOT_CONFIGURATION_MISMATCH",
    )


def _ensure_namespace(root_fd: int, *, facts: _Facts) -> int:
    descriptor = -1
    try:
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
            code="V2R_DURABLE_ANTI_REPLAY_NAMESPACE_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            root_fd,
            facts.state_namespace,
            directory=True,
            code="V2R_DURABLE_ANTI_REPLAY_NAMESPACE_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("V2R_DURABLE_ANTI_REPLAY_NAMESPACE_UNSAFE")
        return descriptor
    except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(
            "V2R_DURABLE_ANTI_REPLAY_NAMESPACE_UNSAFE"
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
            code="V2R_DURABLE_ANTI_REPLAY_RECORDS_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            namespace_fd,
            _RECORDS_DIRECTORY,
            directory=True,
            code="V2R_DURABLE_ANTI_REPLAY_RECORDS_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("V2R_DURABLE_ANTI_REPLAY_RECORDS_UNSAFE")
        return descriptor
    except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(
            "V2R_DURABLE_ANTI_REPLAY_RECORDS_UNSAFE"
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
            code="V2R_DURABLE_ANTI_REPLAY_LOCK_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            namespace_fd,
            _LOCK_FILENAME,
            directory=False,
            code="V2R_DURABLE_ANTI_REPLAY_LOCK_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("V2R_DURABLE_ANTI_REPLAY_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(
            "V2R_DURABLE_ANTI_REPLAY_LOCK_OPEN_FAILED"
        ) from exc


def _validate_namespace_entries(namespace_fd: int) -> None:
    known = {_LOCK_FILENAME, _BINDING_FILENAME, _CURRENT_FILENAME, _RECORDS_DIRECTORY}
    for name in _listdir(namespace_fd, code="V2R_DURABLE_ANTI_REPLAY_NAMESPACE_RESIDUE"):
        if name not in known:
            _fail(
                "V2R_DURABLE_ANTI_REPLAY_TEMP_RESIDUE"
                if _TEMP_NAME_RE.fullmatch(name)
                else "V2R_DURABLE_ANTI_REPLAY_NAMESPACE_RESIDUE"
            )
        _safe_child_metadata(
            namespace_fd,
            name,
            directory=name == _RECORDS_DIRECTORY,
            code="V2R_DURABLE_ANTI_REPLAY_NAMESPACE_UNSAFE",
        )


@contextmanager
def _locked_storage(*, facts: _Facts) -> Iterator[_Storage]:
    root_fd = -1
    namespace_fd = -1
    records_fd = -1
    lock_fd = -1
    try:
        root_fd = _open_secure_root()
        _validate_root_entries(root_fd)
        _ensure_root_configuration_binding(root_fd, facts=facts)
        _validate_root_entries(root_fd)
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


def _write_current_atomic(namespace_fd: int, payload: bytes) -> None:
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_RECORD_BYTES:
        _fail("V2R_DURABLE_ANTI_REPLAY_CURRENT_INVALID")
    temporary = ".current-" + secrets.token_bytes(32).hex() + ".tmp"
    descriptor = -1
    try:
        try:
            _safe_child_metadata(
                namespace_fd,
                _CURRENT_FILENAME,
                directory=False,
                code="V2R_DURABLE_ANTI_REPLAY_CURRENT_UNSAFE",
            )
        except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError as exc:
            if not isinstance(exc.__cause__, FileNotFoundError):
                try:
                    os.stat(_CURRENT_FILENAME, dir_fd=namespace_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                except OSError as inner:
                    raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(
                        "V2R_DURABLE_ANTI_REPLAY_CURRENT_UNSAFE"
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
            _fail("V2R_DURABLE_ANTI_REPLAY_CURRENT_UNSAFE")
        _write_all(descriptor, payload, code="V2R_DURABLE_ANTI_REPLAY_CURRENT_WRITE_FAILED")
        os.fdatasync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(temporary, _CURRENT_FILENAME, src_dir_fd=namespace_fd, dst_dir_fd=namespace_fd)
        _safe_child_metadata(
            namespace_fd,
            _CURRENT_FILENAME,
            directory=False,
            code="V2R_DURABLE_ANTI_REPLAY_CURRENT_UNSAFE",
        )
        os.fsync(namespace_fd)
    except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError:
        raise
    except OSError as exc:
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(
            "V2R_DURABLE_ANTI_REPLAY_CURRENT_WRITE_FAILED"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _record_payload(
    *,
    facts: _Facts,
    sequence: int,
    previous_record_sha256: str,
    correlation_id_sha256: str,
) -> tuple[bytes, str]:
    body = {
        "schema": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "receiving_role": facts.receiving_role,
        "local_site": facts.local_site,
        "local_role": facts.local_role,
        "stage": facts.stage,
        "state_namespace": facts.state_namespace,
        "reservation_prefix": facts.reservation_prefix,
        "v2r_configuration_sha256": facts.v2r_configuration_sha256,
        "v2r_full_configuration_sha256": facts.v2r_full_configuration_sha256,
        "sequence": sequence,
        "previous_record_sha256": previous_record_sha256,
        "correlation_id_sha256": correlation_id_sha256,
    }
    digest = hashlib.sha256(_canonical(body, code="V2R_DURABLE_ANTI_REPLAY_RECORD_INVALID")).hexdigest()
    return (
        _canonical(
            {**body, "record_sha256": digest},
            code="V2R_DURABLE_ANTI_REPLAY_RECORD_INVALID",
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
    code = "V2R_DURABLE_ANTI_REPLAY_RECORD_INVALID"
    decoded = _decode_canonical(payload, code=code)
    required = {
        "schema",
        "version",
        "mode",
        "receiving_role",
        "local_site",
        "local_role",
        "stage",
        "state_namespace",
        "reservation_prefix",
        "v2r_configuration_sha256",
        "v2r_full_configuration_sha256",
        "sequence",
        "previous_record_sha256",
        "correlation_id_sha256",
        "record_sha256",
    }
    if set(decoded) != required:
        _fail(code)
    sequence = _positive(decoded["sequence"], code=code)
    previous = _sha256(decoded["previous_record_sha256"], code=code, permit_zero=True)
    correlation = _sha256(decoded["correlation_id_sha256"], code=code)
    record_sha256 = _sha256(decoded["record_sha256"], code=code)
    if (
        decoded["schema"] != PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_SCHEMA
        or decoded["version"] != _VERSION
        or decoded["mode"] != _MODE
        or decoded["receiving_role"] != facts.receiving_role
        or decoded["local_site"] != facts.local_site
        or decoded["local_role"] != facts.local_role
        or decoded["stage"] != facts.stage
        or decoded["state_namespace"] != facts.state_namespace
        or decoded["reservation_prefix"] != facts.reservation_prefix
        or decoded["v2r_configuration_sha256"] != facts.v2r_configuration_sha256
        or decoded["v2r_full_configuration_sha256"] != facts.v2r_full_configuration_sha256
        or sequence != expected_sequence
        or previous != expected_previous_record_sha256
    ):
        _fail(code)
    expected_payload, expected_digest = _record_payload(
        facts=facts,
        sequence=sequence,
        previous_record_sha256=previous,
        correlation_id_sha256=correlation,
    )
    if payload != expected_payload or record_sha256 != expected_digest:
        _fail(code)
    return _Record(
        sequence=sequence,
        previous_record_sha256=previous,
        record_sha256=record_sha256,
        correlation_id_sha256=correlation,
    )


def _current_payload(*, facts: _Facts, record: _Record) -> bytes:
    return _canonical(
        {
            "schema": PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_SCHEMA,
            "version": _VERSION,
            "mode": _MODE,
            "receiving_role": facts.receiving_role,
            "local_site": facts.local_site,
            "local_role": facts.local_role,
            "stage": facts.stage,
            "state_namespace": facts.state_namespace,
            "reservation_prefix": facts.reservation_prefix,
            "v2r_configuration_sha256": facts.v2r_configuration_sha256,
            "v2r_full_configuration_sha256": facts.v2r_full_configuration_sha256,
            "sequence": record.sequence,
            "record_sha256": record.record_sha256,
            "correlation_id_sha256": record.correlation_id_sha256,
        },
        code="V2R_DURABLE_ANTI_REPLAY_CURRENT_INVALID",
    )


def _load_state(storage: _Storage, *, facts: _Facts) -> tuple[_Record, ...]:
    _write_create_only_at(
        storage.namespace_fd,
        _BINDING_FILENAME,
        facts.binding_payload,
        code="V2R_DURABLE_ANTI_REPLAY_BINDING_MISMATCH",
    )
    records: list[tuple[int, str, str]] = []
    for name in _listdir(storage.records_fd, code="V2R_DURABLE_ANTI_REPLAY_RECORDS_RESIDUE"):
        match = _RECORD_NAME_RE.fullmatch(name)
        if match is None:
            _fail(
                "V2R_DURABLE_ANTI_REPLAY_TEMP_RESIDUE"
                if _TEMP_NAME_RE.fullmatch(name)
                else "V2R_DURABLE_ANTI_REPLAY_RECORDS_RESIDUE"
            )
        _safe_child_metadata(
            storage.records_fd,
            name,
            directory=False,
            code="V2R_DURABLE_ANTI_REPLAY_RECORD_UNSAFE",
        )
        records.append((int(match.group(1)), match.group(2), name))
    if len(records) > _MAX_RECORDS:
        _fail("V2R_DURABLE_ANTI_REPLAY_RECORD_LIMIT")
    records.sort()
    expected_sequence = 1
    previous = _ZERO_SHA256
    result: list[_Record] = []
    seen_correlations: set[str] = set()
    for sequence, file_correlation, name in records:
        if sequence != expected_sequence:
            _fail("V2R_DURABLE_ANTI_REPLAY_RECORD_ROLLBACK")
        record = _record_from_payload(
            _read_file_at(
                storage.records_fd,
                name,
                code="V2R_DURABLE_ANTI_REPLAY_RECORD_INVALID",
            ),
            facts=facts,
            expected_sequence=expected_sequence,
            expected_previous_record_sha256=previous,
        )
        if record.correlation_id_sha256 != file_correlation or record.correlation_id_sha256 in seen_correlations:
            _fail("V2R_DURABLE_ANTI_REPLAY_RECORD_INVALID")
        seen_correlations.add(record.correlation_id_sha256)
        result.append(record)
        previous = record.record_sha256
        expected_sequence += 1
    try:
        current = _read_file_at(
            storage.namespace_fd,
            _CURRENT_FILENAME,
            code="V2R_DURABLE_ANTI_REPLAY_CURRENT_INVALID",
        )
    except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            if result:
                _fail("V2R_DURABLE_ANTI_REPLAY_CURRENT_ROLLBACK")
            return tuple()
        raise
    if not result or current != _current_payload(facts=facts, record=result[-1]):
        _fail("V2R_DURABLE_ANTI_REPLAY_CURRENT_ROLLBACK")
    return tuple(result)


def _checkpoint(
    checkpoint: object,
    *,
    facts: _Facts,
    record: _Record | None,
) -> None:
    callback = getattr(checkpoint, "attest_v2r_roundtrip_anti_replay_state", None)
    if not callable(callback):
        _fail("V2R_DURABLE_ANTI_REPLAY_CHECKPOINT_MISSING")
    try:
        result = callback(
            binding_sha256=facts.binding_sha256,
            receiving_role=facts.receiving_role,
            state_namespace=facts.state_namespace,
            reservation_prefix=facts.reservation_prefix,
            sequence=0 if record is None else record.sequence,
            previous_record_sha256=_ZERO_SHA256 if record is None else record.previous_record_sha256,
            record_sha256=_ZERO_SHA256 if record is None else record.record_sha256,
        )
    except PhysicalWalV2rWitnessRoundtripDurableAntiReplayError:
        raise
    except Exception as exc:
        raise PhysicalWalV2rWitnessRoundtripDurableAntiReplayError(
            "V2R_DURABLE_ANTI_REPLAY_CHECKPOINT_REJECTED"
        ) from exc
    if result is not None:
        _fail("V2R_DURABLE_ANTI_REPLAY_CHECKPOINT_INVALID")


def _require_checkpoint_callback(checkpoint: object) -> None:
    if not callable(getattr(checkpoint, "attest_v2r_roundtrip_anti_replay_state", None)):
        _fail("V2R_DURABLE_ANTI_REPLAY_CHECKPOINT_MISSING")


class PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistry:
    """Root-gated append-only reservation implementation for one receiver.

    A receipt only states that a correlation is burned in this receiving
    role's durable namespace.  It cannot authorize the envelope, carrier,
    Object Storage, a writer, a promotion, a traffic transition, or execution.
    """

    def __init__(
        self,
        config: PhysicalWalV2rWitnessRoundtripDurableAntiReplayRegistryConfig,
        *,
        rollback_checkpoint: PhysicalWalV2rWitnessRoundtripDurableAntiReplayCheckpoint | None,
    ) -> None:
        self._config = config
        self._rollback_checkpoint = rollback_checkpoint

    def reserve_before_receive(
        self,
        *,
        roundtrip_config: v2r.PhysicalWalV2rWitnessRoundtripConfig,
        stage: str,
        correlation_id: str,
    ) -> PhysicalWalV2rWitnessRoundtripDurableAntiReplayReservationReceipt:
        """Permanently burn a V2R correlation before accepting that hop.

        The local record and monotonic checkpoint are durable before a receipt
        is returned.  If the later receiver action fails or is ambiguous, the
        future adapter must keep this reservation and must not retry the same
        correlation.  The exact V2R config and local contractual stage are
        rechecked on every call.
        """

        facts = _facts(self._config)
        _require_root()
        _require_checkpoint_callback(self._rollback_checkpoint)
        supplied_payload, supplied_wire_digest, supplied_full_digest = _validated_v2r_configuration_payload(
            roundtrip_config,
            code="V2R_DURABLE_ANTI_REPLAY_V2R_CONFIGURATION_MISMATCH",
        )
        if (
            supplied_payload != facts.v2r_full_configuration_payload
            or supplied_wire_digest != facts.v2r_configuration_sha256
            or supplied_full_digest != facts.v2r_full_configuration_sha256
        ):
            _fail("V2R_DURABLE_ANTI_REPLAY_V2R_CONFIGURATION_MISMATCH")
        if type(stage) is not str or stage != facts.stage:
            _fail("V2R_DURABLE_ANTI_REPLAY_STAGE_ROLE_MISMATCH")
        if type(correlation_id) is not str or _CORRELATION_RE.fullmatch(correlation_id) is None:
            _fail("V2R_DURABLE_ANTI_REPLAY_CORRELATION_INVALID")
        correlation_sha256 = hashlib.sha256(correlation_id.encode("ascii")).hexdigest()
        with _locked_storage(facts=facts) as storage:
            records = _load_state(storage, facts=facts)
            previous = records[-1] if records else None
            _checkpoint(self._rollback_checkpoint, facts=facts, record=previous)
            if any(record.correlation_id_sha256 == correlation_sha256 for record in records):
                _fail("V2R_DURABLE_ANTI_REPLAY_CORRELATION_REUSED")
            sequence = 1 if previous is None else previous.sequence + 1
            previous_sha256 = _ZERO_SHA256 if previous is None else previous.record_sha256
            payload, record_sha256 = _record_payload(
                facts=facts,
                sequence=sequence,
                previous_record_sha256=previous_sha256,
                correlation_id_sha256=correlation_sha256,
            )
            _write_create_only_at(
                storage.records_fd,
                f"{sequence:020d}-{correlation_sha256}.json",
                payload,
                code="V2R_DURABLE_ANTI_REPLAY_RECORD_WRITE_FAILED",
            )
            record = _Record(
                sequence=sequence,
                previous_record_sha256=previous_sha256,
                record_sha256=record_sha256,
                correlation_id_sha256=correlation_sha256,
            )
            _write_current_atomic(storage.namespace_fd, _current_payload(facts=facts, record=record))
            _checkpoint(self._rollback_checkpoint, facts=facts, record=record)
            return PhysicalWalV2rWitnessRoundtripDurableAntiReplayReservationReceipt(
                schema=PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_DURABLE_ANTI_REPLAY_SCHEMA,
                receiving_role=facts.receiving_role,
                local_site=facts.local_site,
                local_role=facts.local_role,
                stage=facts.stage,
                state_namespace=facts.state_namespace,
                reservation_prefix=facts.reservation_prefix,
                v2r_configuration_sha256=facts.v2r_configuration_sha256,
                v2r_full_configuration_sha256=facts.v2r_full_configuration_sha256,
                correlation_id_sha256=correlation_sha256,
                reservation_sequence=sequence,
                reservation_record_sha256=record_sha256,
            )
