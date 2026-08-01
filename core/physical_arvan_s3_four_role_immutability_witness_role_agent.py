"""Root-only, one-role execution agent for Witness immutable-probe approvals.

This is deliberately a *local* runner.  It has no peer address, no HTTP/SSH
client, no Object-Storage transport, and no four-role callback registry.  A
separately reviewed inbox/outbox may hand it an opaque Witness approval; the
agent verifies that approval, reserves its digest durably, invokes only its
one role-local collector, signs the semantic receipt with its fixed local
attestation key, and leaves that receipt in its local outbox.

The durable reservation is intentionally fail-closed.  If a process dies
after the irreversible immutable probe but before it writes the receipt, the
same approval is not retried automatically.  Witness operators must inspect
the local root-owned reservation/receipt pair rather than risk duplicate
mutation evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import stat

from core import physical_arvan_s3_four_role_immutability_role_local_collector as _collector
from core import physical_arvan_s3_four_role_immutability_witness_orchestration as _orchestration
from core import physical_arvan_s3_role_profiles as _profiles


__all__ = (
    "FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_ATTESTATION_KEY_FILE_BY_ROLE",
    "FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_DIR_BY_ROLE",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_SCHEMA",
    "PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentConfig",
    "PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError",
    "RootOwnedPhysicalArvanS3FourRoleImmutabilityWitnessRoleAgent",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-immutability-witness-role-agent-v1"
)
PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_DEFAULT_ENABLED = False

_ROLE_ORDER = (
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE,
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE,
)
_ROLE_ACTION_PROFILE = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: _profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: _profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: _profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: _profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
}

FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_ATTESTATION_KEY_FILE_BY_ROLE = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-four-role-immutability-fi-publisher-attestation.key"
    ),
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-four-role-immutability-ir-receiver-attestation.key"
    ),
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-four-role-immutability-ir-publisher-attestation.key"
    ),
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: Path(
        "/etc/trading-bot/security/arvan-s3-four-role-immutability-fi-receiver-attestation.key"
    ),
}
FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_DIR_BY_ROLE = {
    _profiles.ARVAN_S3_FI_PUBLISHER_ROLE: Path(
        "/var/lib/trading-bot/security/arvan-s3-four-role-immutability-fi-publisher-agent"
    ),
    _profiles.ARVAN_S3_IR_RECEIVER_ROLE: Path(
        "/var/lib/trading-bot/security/arvan-s3-four-role-immutability-ir-receiver-agent"
    ),
    _profiles.ARVAN_S3_IR_PUBLISHER_ROLE: Path(
        "/var/lib/trading-bot/security/arvan-s3-four-role-immutability-ir-publisher-agent"
    ),
    _profiles.ARVAN_S3_FI_RECEIVER_ROLE: Path(
        "/var/lib/trading-bot/security/arvan-s3-four-role-immutability-fi-receiver-agent"
    ),
}


class PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError(ValueError):
    """A root-only local Witness request could not be executed safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentConfig:
    """In-memory, default-off configuration for exactly one machine role."""

    schema: str = PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_SCHEMA
    role: str = ""
    binding: _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessBinding | None = field(
        default=None,
        repr=False,
    )
    collector: _collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    enabled: bool = PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_DEFAULT_ENABLED


@dataclass(frozen=True)
class _AgentFacts:
    role: str
    binding: _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessBinding
    collector: _collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector
    key_path: Path
    state_root: Path


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError(code)


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_REQUIRES_ROOT")
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_REQUIRES_ROOT")


def _facts(value: object) -> _AgentFacts:
    if type(value) is not PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentConfig:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_CONFIG_INVALID")
    config = value
    if (
        config.schema != PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_SCHEMA
        or type(config.enabled) is not bool
        or config.enabled is not True
        or config.role not in _ROLE_ORDER
        or type(config.collector)
        is not _collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_CONFIG_INVALID")
    try:
        binding = _orchestration._binding(config.binding)
    except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_CONFIG_INVALID")
    return _AgentFacts(
        role=config.role,
        binding=binding,
        collector=config.collector,
        key_path=FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_ATTESTATION_KEY_FILE_BY_ROLE[
            config.role
        ],
        state_root=FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_DIR_BY_ROLE[
            config.role
        ],
    )


def _canonical_now(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc or value.microsecond != 0:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_TIME_INVALID")
    return value


def _host_now() -> datetime:
    """Read the host-owned UTC clock; callers cannot backdate execution."""

    try:
        return _canonical_now(datetime.now(timezone.utc).replace(microsecond=0))
    except Exception:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_TIME_INVALID")


def _open_secure_directory(path: Path, *, code: str) -> int:
    """Open a fixed root-owned directory once and operate below its fd."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail(code)
    try:
        metadata = os.fstat(descriptor)
    except OSError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        _fail(code)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        try:
            os.close(descriptor)
        except OSError:
            pass
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_UNSAFE")
    return descriptor


def _secure_key(path: Path, *, expected_public_key: bytes) -> object:
    parent = _open_secure_directory(
        path.parent,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_KEY_UNAVAILABLE",
    )
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
    except OSError:
        try:
            os.close(parent)
        except OSError:
            pass
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_KEY_UNAVAILABLE")
    try:
        metadata = os.fstat(descriptor)
    except OSError:
        try:
            os.close(descriptor)
            os.close(parent)
        except OSError:
            pass
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_KEY_UNAVAILABLE")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size != 32
    ):
        try:
            os.close(descriptor)
            os.close(parent)
        except OSError:
            pass
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_KEY_UNSAFE")
    try:
        value = os.read(descriptor, 33)
        if os.read(descriptor, 1):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_KEY_UNSAFE")
    except PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError:
        raise
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_KEY_UNAVAILABLE")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.close(parent)
        except OSError:
            pass
    if len(value) != 32:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_KEY_UNSAFE")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        signer = Ed25519PrivateKey.from_private_bytes(value)
        actual = signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (ImportError, ValueError):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_KEY_UNSAFE")
    if actual != expected_public_key:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_KEY_NOT_PINNED")
    return signer


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        try:
            written = os.write(descriptor, value[offset:])
        except OSError:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_UNAVAILABLE")
        if type(written) is not int or written <= 0:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_UNAVAILABLE")
        offset += written


def _atomic_new_at(directory: int, name: str, value: bytes, *, code: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=directory)
    except FileExistsError:
        _fail(code)
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_UNAVAILABLE")
    try:
        _write_all(descriptor, value)
        os.fsync(descriptor)
    except PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError:
        raise
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_UNAVAILABLE")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _reserve_once(*, state_root: Path, approval_sha256: str) -> None:
    directory = _open_secure_directory(
        state_root,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_UNAVAILABLE",
    )
    try:
        _atomic_new_at(
            directory,
            approval_sha256 + ".reserved",
            (approval_sha256 + "\n").encode("ascii"),
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_REPLAY_OR_IN_PROGRESS",
        )
        # This is the durability boundary before the irreversible local S3
        # probe.  Leaf fsync alone is not enough to persist its directory
        # entry across a crash.
        os.fsync(directory)
    except PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError:
        raise
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_UNAVAILABLE")
    finally:
        try:
            os.close(directory)
        except OSError:
            pass


def _write_receipt(*, state_root: Path, approval_sha256: str, receipt: bytes) -> None:
    directory = _open_secure_directory(
        state_root,
        code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_UNAVAILABLE",
    )
    try:
        _atomic_new_at(
            directory,
            approval_sha256 + ".receipt",
            receipt,
            code="ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_RECEIPT_COLLISION",
        )
        os.fsync(directory)
    except PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError:
        raise
    except OSError:
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_UNAVAILABLE")
    finally:
        try:
            os.close(directory)
        except OSError:
            pass


def _binding_identity(
    binding: _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessBinding, role: str
) -> str:
    return getattr(binding.preflight_binding, _orchestration._ROLE_IDENTITY_FIELD[role])


def _binding_signer(
    binding: _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessBinding, role: str
) -> bytes:
    return getattr(binding.live_iam_binding, _orchestration._ROLE_SIGNER_FIELD[role])


def _require_local_approval(
    *,
    facts: _AgentFacts,
    verified: _orchestration.VerifiedPhysicalArvanS3FourRoleImmutabilityWitnessApproval,
    now: datetime,
) -> None:
    request = verified.approval.request
    if (
        verified.approval.stage != facts.role
        or request.role != facts.role
        or request.identity_sha256 != _binding_identity(facts.binding, facts.role)
        or request.observed_at != verified.approval.issued_at
        or now - verified.approval.issued_at
        > timedelta(
            seconds=_collector._runtime.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_MAX_TRANSPORT_GRACE_SECONDS
        )
    ):
        _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_APPROVAL_NOT_LOCAL")


class RootOwnedPhysicalArvanS3FourRoleImmutabilityWitnessRoleAgent:
    """Execute exactly one fresh Witness approval on exactly one local role."""

    def __init__(
        self,
        config: PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentConfig = (
            PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentConfig()
        ),
    ) -> None:
        # Construction performs no root check, file read, credential open,
        # state write, or collector action.
        self._config = config

    def execute(self, *, approval: bytes) -> bytes:
        """Verify, reserve, collect, sign, and locally persist one receipt.

        No remote delivery occurs here.  ``approval`` is only an opaque
        Witness-signed byte string, and the result is only an opaque local
        receipt for an external Witness-mediated outbox to relay.
        """

        _require_root()
        facts = _facts(self._config)
        now = _host_now()
        try:
            verified = _orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
                approval,
                binding=facts.binding,
                observed_at=now,
            )
        except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_APPROVAL_INVALID")
        _require_local_approval(facts=facts, verified=verified, now=now)
        request = verified.approval.request
        # Prove the locally configured S3 credential belongs to this agent's
        # one pinned role before reserving or invoking its collector.
        try:
            identity = facts.collector.identity_projection()
        except Exception:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_LOCAL_IDENTITY_INVALID")
        if (
            identity.role != facts.role
            or identity.identity_sha256 != request.identity_sha256
            or identity.action_profile != _ROLE_ACTION_PROFILE[facts.role]
        ):
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_LOCAL_IDENTITY_INVALID")
        # Load and pin the local receipt signer before the irreversible probe.
        # A missing/rotated key must fail before reservation/collector rather
        # than leave an intentionally non-retryable mutation without a receipt.
        signer = _secure_key(
            facts.key_path,
            expected_public_key=_binding_signer(facts.binding, facts.role),
        )
        _reserve_once(state_root=facts.state_root, approval_sha256=verified.approval.raw_sha256)
        # Re-read the private host clock and revalidate the signed approval
        # immediately before the irreversible collector.  A timeout while
        # opening the local identity or reserving the marker never turns into
        # a late execution; the durable marker remains for manual recovery.
        now = _host_now()
        try:
            verified_after_reservation = (
                _orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
                    approval,
                    binding=facts.binding,
                    observed_at=now,
                )
            )
        except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_APPROVAL_INVALID")
        if verified_after_reservation.approval.raw_sha256 != verified.approval.raw_sha256:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_APPROVAL_INVALID")
        _require_local_approval(
            facts=facts,
            verified=verified_after_reservation,
            now=now,
        )
        verified = verified_after_reservation
        request = verified.approval.request
        try:
            local_readback = facts.collector.collect(request)
        except Exception:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_COLLECTOR_FAILED")
        try:
            receipt = _orchestration.seal_physical_arvan_s3_four_role_immutability_role_receipt(
                approval=verified,
                binding=facts.binding,
                observed_at=now,
                local_readback=local_readback,
                role_signer=signer,
            )
        except _orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError:
            _fail("ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_RECEIPT_INVALID")
        _write_receipt(
            state_root=facts.state_root,
            approval_sha256=verified.approval.raw_sha256,
            receipt=receipt,
        )
        return receipt
