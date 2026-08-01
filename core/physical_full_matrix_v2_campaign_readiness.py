"""V2-only local readiness aggregation for the physical Full Matrix.

This is a separate protocol generation.  It intentionally does not import,
adapt, or extend ``physical_full_matrix_campaign_readiness``: that module has
retired V1 single-object and V1 strict-ACK slots which must never contribute
to a V2 result.  A positive report below means only that the *local V2 ACK
chain* was observed and revalidated.  It is not authority to start, write,
promote, deploy, mutate Object Storage, or execute a matrix phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.physical_full_matrix_v2_ack_chain import (
    PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_SCHEMA,
    PhysicalFullMatrixV2AckChainConfig,
    PhysicalFullMatrixV2AckChainError,
    VerifiedPhysicalFullMatrixV2AckChain,
    require_verified_physical_full_matrix_v2_ack_chain,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_BLOCKED",
    "PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED",
    "PHYSICAL_FULL_MATRIX_V2_REQUIRED_READINESS_SLOTS",
    "PhysicalFullMatrixV2CampaignBinding",
    "PhysicalFullMatrixV2CampaignInputs",
    "PhysicalFullMatrixV2CampaignReadiness",
    "PhysicalFullMatrixV2CampaignReadinessConfig",
    "PhysicalFullMatrixV2CampaignReadinessError",
    "VerifiedPhysicalFullMatrixV2CampaignReadiness",
    "assess_physical_full_matrix_v2_campaign_readiness",
    "mint_verified_physical_full_matrix_v2_campaign_readiness",
    "require_verified_physical_full_matrix_v2_campaign_readiness",
)


PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_SCHEMA = (
    "gold-trade-physical-full-matrix-v2-campaign-readiness-v1"
)
PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_BLOCKED = "blocked"
PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED = (
    "v2-local-ack-chain-observed"
)
PHYSICAL_FULL_MATRIX_V2_REQUIRED_READINESS_SLOTS = (
    "v2-strict-remote-ack-chain",
)

_CAPABILITY = object()
_ZERO_SHA256 = "0" * 64


class PhysicalFullMatrixV2CampaignReadinessError(ValueError):
    """V2 readiness is not local, complete, fresh, or provenance-bound."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalFullMatrixV2CampaignBinding:
    """Redacted exact identity pins for one initial V2 writer direction."""

    campaign_id: str
    release_sha: str
    source_site: str
    destination_site: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV2CampaignReadinessConfig:
    """Default-off V2 policy; it intentionally contains no V1 input slot."""

    binding: PhysicalFullMatrixV2CampaignBinding | None = None
    ack_chain_config: PhysicalFullMatrixV2AckChainConfig | None = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalFullMatrixV2CampaignInputs:
    """One V2 chain plus an explicit fence for accidental legacy artifacts."""

    v2_ack_chain: VerifiedPhysicalFullMatrixV2AckChain | None = None
    legacy_runner_artifacts: object = ()


@dataclass(frozen=True)
class PhysicalFullMatrixV2CampaignReadiness:
    """Public diagnostic report, deliberately not an execution capability."""

    schema: str
    status: str
    campaign_id: str | None
    release_sha: str | None
    binding_sha256: str | None
    observed_slots: tuple[str, ...]
    reason_codes: tuple[str, ...]
    external_execution_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalFullMatrixV2CampaignReadiness:
    """Opaque provenance for a positive V2 local observation only."""

    report: PhysicalFullMatrixV2CampaignReadiness
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_READINESS_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV2CampaignReadinessConfig
    inputs: PhysicalFullMatrixV2CampaignInputs
    report: PhysicalFullMatrixV2CampaignReadiness


_STATES: WeakKeyDictionary[VerifiedPhysicalFullMatrixV2CampaignReadiness, _State] = (
    WeakKeyDictionary()
)


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV2CampaignReadinessError(code)


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _binding(value: object) -> PhysicalFullMatrixV2CampaignBinding:
    if type(value) is not PhysicalFullMatrixV2CampaignBinding:
        _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_BINDING_INVALID")
    if type(value.campaign_id) is not str or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_BINDING_INVALID")
    if type(value.release_sha) is not str or RELEASE_SHA_RE.fullmatch(value.release_sha) is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_BINDING_INVALID")
    if (
        type(value.source_site) is not str
        or type(value.destination_site) is not str
        or value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
        or type(value.writer_holder_site) is not str
        or value.writer_holder_site != value.source_site
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_BINDING_INVALID")
    _sha256(value.route_commitment_sha256, code="PHYSICAL_FULL_MATRIX_V2_READINESS_BINDING_INVALID")
    _sha256(value.four_role_binding_sha256, code="PHYSICAL_FULL_MATRIX_V2_READINESS_BINDING_INVALID")
    _sha256(value.witnessed_term_proof_sha256, code="PHYSICAL_FULL_MATRIX_V2_READINESS_BINDING_INVALID")
    if (
        type(value.writer_epoch) is not int
        or not 1 <= value.writer_epoch <= 2**31 - 1
        or type(value.writer_lease_id) is not str
        or LEASE_ID_RE.fullmatch(value.writer_lease_id) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_BINDING_INVALID")
    return value


def _binding_sha256(value: PhysicalFullMatrixV2CampaignBinding) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema": PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_SCHEMA,
                "campaign_id": value.campaign_id,
                "release_sha": value.release_sha,
                "source_site": value.source_site,
                "destination_site": value.destination_site,
                "route_commitment_sha256": value.route_commitment_sha256,
                "four_role_binding_sha256": value.four_role_binding_sha256,
                "writer_holder_site": value.writer_holder_site,
                "writer_epoch": value.writer_epoch,
                "writer_lease_id": value.writer_lease_id,
                "witnessed_term_proof_sha256": value.witnessed_term_proof_sha256,
            }
        )
    ).hexdigest()


def _config(
    value: object,
) -> tuple[PhysicalFullMatrixV2CampaignBinding, PhysicalFullMatrixV2AckChainConfig]:
    if (
        type(value) is not PhysicalFullMatrixV2CampaignReadinessConfig
        or value.enabled is not True
        or type(value.ack_chain_config) is not PhysicalFullMatrixV2AckChainConfig
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_CONFIG_INVALID")
    return _binding(value.binding), value.ack_chain_config


def _legacy_artifacts_present(value: object) -> bool:
    """Reject rather than parse anything from a historical runner surface."""

    return value not in (None, (), [], "")


def _report(
    *,
    binding: PhysicalFullMatrixV2CampaignBinding | None,
    slots: set[str],
    reasons: set[str],
) -> PhysicalFullMatrixV2CampaignReadiness:
    positive = not reasons and tuple(sorted(slots)) == PHYSICAL_FULL_MATRIX_V2_REQUIRED_READINESS_SLOTS
    return PhysicalFullMatrixV2CampaignReadiness(
        schema=PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_SCHEMA,
        status=(
            PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
            if positive
            else PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_BLOCKED
        ),
        campaign_id=None if binding is None else binding.campaign_id,
        release_sha=None if binding is None else binding.release_sha,
        binding_sha256=None if binding is None else _binding_sha256(binding),
        observed_slots=tuple(sorted(slots)),
        reason_codes=tuple(sorted(reasons)),
    )


def assess_physical_full_matrix_v2_campaign_readiness(
    config: object,
    inputs: object,
    *,
    now: datetime,
) -> PhysicalFullMatrixV2CampaignReadiness:
    """Assess only V2 opaque capabilities without performing any I/O."""

    reasons: set[str] = set()
    slots: set[str] = set()
    try:
        _utc(now)
    except PhysicalFullMatrixV2CampaignReadinessError:
        return _report(binding=None, slots=slots, reasons={"invalid-assessment-clock"})
    try:
        binding, ack_chain_config = _config(config)
    except PhysicalFullMatrixV2CampaignReadinessError as exc:
        return _report(
            binding=None,
            slots=slots,
            reasons={
                "driver-disabled"
                if exc.code == "PHYSICAL_FULL_MATRIX_V2_READINESS_CONFIG_INVALID"
                else "invalid-v2-campaign-binding"
            },
        )
    if type(inputs) is not PhysicalFullMatrixV2CampaignInputs:
        return _report(binding=binding, slots=slots, reasons={"invalid-v2-campaign-inputs"})
    if _legacy_artifacts_present(inputs.legacy_runner_artifacts):
        return _report(binding=binding, slots=slots, reasons={"legacy-v1-artifact-rejected"})
    if inputs.v2_ack_chain is None:
        reasons.add("missing-v2-strict-remote-ack-chain")
    elif type(inputs.v2_ack_chain) is not VerifiedPhysicalFullMatrixV2AckChain:
        reasons.add("v2-strict-remote-ack-chain-mismatch")
    else:
        try:
            chain = require_verified_physical_full_matrix_v2_ack_chain(
                inputs.v2_ack_chain,
                config=ack_chain_config,
                now=now,
            )
            if (
                chain.schema != PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_SCHEMA
                or chain.campaign_id != binding.campaign_id
                or chain.release_sha != binding.release_sha
                or chain.source_site != binding.source_site
                or chain.destination_site != binding.destination_site
                or chain.route_commitment_sha256 != binding.route_commitment_sha256
                or chain.four_role_binding_sha256 != binding.four_role_binding_sha256
                or chain.writer_holder_site != binding.writer_holder_site
                or chain.writer_epoch != binding.writer_epoch
                or chain.writer_lease_id != binding.writer_lease_id
                or chain.witnessed_term_proof_sha256
                != binding.witnessed_term_proof_sha256
                or chain.recovery_authorized is not False
                or chain.promotion_authorized is not False
                or chain.execution_authorized is not False
            ):
                _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_ACK_CHAIN_MISMATCH")
            slots.add("v2-strict-remote-ack-chain")
        except (
            PhysicalFullMatrixV2AckChainError,
            PhysicalFullMatrixV2CampaignReadinessError,
            TypeError,
            ValueError,
        ):
            reasons.add("v2-strict-remote-ack-chain-mismatch")
    return _report(binding=binding, slots=slots, reasons=reasons)


def _positive(value: object, *, code: str) -> PhysicalFullMatrixV2CampaignReadiness:
    if (
        type(value) is not PhysicalFullMatrixV2CampaignReadiness
        or value.schema != PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_SCHEMA
        or value.status
        != PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        or value.reason_codes != ()
        or value.observed_slots != PHYSICAL_FULL_MATRIX_V2_REQUIRED_READINESS_SLOTS
        or type(value.campaign_id) is not str
        or type(value.release_sha) is not str
        or type(value.binding_sha256) is not str
        or value.external_execution_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail(code)
    return value


def mint_verified_physical_full_matrix_v2_campaign_readiness(
    *,
    config: PhysicalFullMatrixV2CampaignReadinessConfig,
    inputs: PhysicalFullMatrixV2CampaignInputs,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2CampaignReadiness:
    """Mint process-local provenance only for a fully positive V2 report."""

    report = _positive(
        assess_physical_full_matrix_v2_campaign_readiness(config, inputs, now=now),
        code="PHYSICAL_FULL_MATRIX_V2_READINESS_POSITIVE_REQUIRED",
    )
    result = VerifiedPhysicalFullMatrixV2CampaignReadiness(report=report)
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = _State(config=config, inputs=inputs, report=report)
    return result


def require_verified_physical_full_matrix_v2_campaign_readiness(
    value: object,
    *,
    now: datetime | None = None,
) -> PhysicalFullMatrixV2CampaignReadiness:
    """Return only an untampered, current V2 local readiness report."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV2CampaignReadiness
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None or value.report is not state.report:
        _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_CAPABILITY_REQUIRED")
    report = _positive(
        state.report,
        code="PHYSICAL_FULL_MATRIX_V2_READINESS_CAPABILITY_TAMPERED",
    )
    if now is None:
        return report
    rechecked = _positive(
        assess_physical_full_matrix_v2_campaign_readiness(
            state.config,
            state.inputs,
            now=now,
        ),
        code="PHYSICAL_FULL_MATRIX_V2_READINESS_REVALIDATION_BLOCKED",
    )
    if rechecked != report:
        _fail("PHYSICAL_FULL_MATRIX_V2_READINESS_REVALIDATION_MISMATCH")
    return report
