"""Focused contract tests for the isolated Witnessed-V2 Full-Matrix V4 driver.

The root-owned phase adapter is intentionally simulated here.  This suite
tests the driver’s semantic gate and receipt chain; it never simulates a host,
network, provider, storage client, database, or a writer promotion.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import pickle
from typing import Callable
from uuid import UUID
import unittest
from unittest.mock import patch

from core import physical_full_matrix_execution_driver_v4 as driver
from core import (
    physical_full_matrix_v2_gen2_witnessed_campaign_readiness as gen2_readiness_owner,
)
from core import physical_full_matrix_v2_witnessed_campaign_readiness as gen1_readiness_owner
from core.physical_full_matrix_v2_gen2_witnessed_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS,
    PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness,
    VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness,
)
from core.physical_full_matrix_v2_witnessed_campaign_readiness import (
    VerifiedPhysicalFullMatrixV2WitnessedCampaignReadiness,
)
from tests.test_physical_full_matrix_v2_gen2_witnessed_ack_chain import (
    Gen2WitnessedAckChainFixture,
)
from tests import test_physical_full_matrix_v2_witnessed_campaign_readiness as gen1_readiness_tests


NOW = datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_execution_driver_v4.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _binding(
    *,
    direction: tuple[str, str] = ("webapp_fi", "webapp_ir"),
    epoch: int = 7,
    suffix: str = "normal",
    four_role_binding_sha256: str | None = None,
    roundtrip_configuration_sha256: str | None = None,
) -> driver.PhysicalFullMatrixV4ExecutionBinding:
    source, destination = direction
    return driver.PhysicalFullMatrixV4ExecutionBinding(
        campaign_id="physical-full-matrix-v4-20260731",
        release_sha="a" * 40,
        readiness_binding_sha256=_hash(f"readiness-{suffix}"),
        route_commitment_sha256=_hash(f"route-{suffix}"),
        four_role_binding_sha256=(
            _hash("four-role")
            if four_role_binding_sha256 is None
            else four_role_binding_sha256
        ),
        writer_holder_site=source,
        writer_epoch=epoch,
        writer_lease_id=f"writer-lease-v4-{suffix}-000001",
        witnessed_term_proof_sha256=_hash(f"term-{suffix}"),
        source_site=source,
        destination_site=destination,
        roundtrip_attestation_sha256=_hash(f"roundtrip-{suffix}"),
        roundtrip_configuration_sha256=(
            _hash(f"configuration-{suffix}")
            if roundtrip_configuration_sha256 is None
            else roundtrip_configuration_sha256
        ),
        witness_transition_id=f"witness-transition-v4-{suffix}-000001",
        witness_sequence=epoch + 10,
    )


def _report_for(
    binding: driver.PhysicalFullMatrixV4ExecutionBinding,
) -> PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    return PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness(
        schema=PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
        status=(
            PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        ),
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        binding_sha256=binding.readiness_binding_sha256,
        observed_slots=PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS,
        reason_codes=(),
    )


def _opaque_readiness(
    binding: driver.PhysicalFullMatrixV4ExecutionBinding,
) -> VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    # This is deliberately not a real owner-minted capability.  The local
    # verifier is patched only inside this simulated semantic-driver suite;
    # a separate integration test covers genuine Gen2 readiness.
    return VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness(
        report=_report_for(binding)
    )


def _evidence(
    binding: driver.PhysicalFullMatrixV4ExecutionBinding,
) -> driver.PhysicalFullMatrixV4ReadinessEvidence:
    return driver.PhysicalFullMatrixV4ReadinessEvidence(
        binding=binding,
        readiness=_opaque_readiness(binding),
    )


def _successor(
    current: driver.PhysicalFullMatrixV4ExecutionBinding,
) -> driver.PhysicalFullMatrixV4ExecutionBinding:
    direction = (
        ("webapp_ir", "webapp_fi")
        if (current.source_site, current.destination_site) == ("webapp_fi", "webapp_ir")
        else ("webapp_fi", "webapp_ir")
    )
    return _binding(
        direction=direction,
        epoch=current.writer_epoch + 1,
        suffix=f"epoch{current.writer_epoch + 1}",
        four_role_binding_sha256=current.four_role_binding_sha256,
        roundtrip_configuration_sha256=current.roundtrip_configuration_sha256,
    )


def _forge_anchor_proof(
    proof: driver.PhysicalFullMatrixV4EffectStartAnchorProof,
    *,
    capability: object,
) -> driver.PhysicalFullMatrixV4EffectStartAnchorProof:
    """Build a lookalike only for provenance-rejection tests."""

    return driver.PhysicalFullMatrixV4EffectStartAnchorProof(
        schema=proof.schema,
        run_id=proof.run_id,
        plan_sha256=proof.plan_sha256,
        phase=proof.phase,
        effect_key=proof.effect_key,
        phase_request_sha256=proof.phase_request_sha256,
        binding=proof.binding,
        claim_id=proof.claim_id,
        journaled_effect_start_identity_sha256=(
            proof.journaled_effect_start_identity_sha256
        ),
        journal_binding_sha256=proof.journal_binding_sha256,
        baseline_plan_binding_sha256=proof.baseline_plan_binding_sha256,
        anchor_genesis_sequence=proof.anchor_genesis_sequence,
        anchor_genesis_head_sha256=proof.anchor_genesis_head_sha256,
        anchor_previous_sequence=proof.anchor_previous_sequence,
        anchor_previous_head_sha256=proof.anchor_previous_head_sha256,
        anchor_sequence=proof.anchor_sequence,
        anchor_head_sha256=proof.anchor_head_sha256,
        anchor_commitment_sha256=proof.anchor_commitment_sha256,
        anchor_attestation_sha256=proof.anchor_attestation_sha256,
        anchor_local_previous_record_sha256=(
            proof.anchor_local_previous_record_sha256
        ),
        anchor_local_event_sha256=proof.anchor_local_event_sha256,
        anchor_occurred_at=proof.anchor_occurred_at,
        capability=capability,
    )


class _Journal:
    """In-memory model of the injected append-only root journal."""

    def __init__(self) -> None:
        self.receipts: list[bytes] = []
        self.claims: set[tuple[UUID, str, int, str]] = set()
        self.effect_starts: dict[
            tuple[UUID, str, int, str], driver.PhysicalFullMatrixV4EffectStart
        ] = {}
        self._start_anchors: dict[tuple[UUID, str, int, str], dict[str, object]] = {}
        self._completion_anchors: dict[tuple[UUID, str, int, str], dict[str, object]] = {}
        self._anchor_sequence = 0
        self._anchor_head_sha256 = "0" * 64
        self.events: list[str] = []

    def _advance_anchor(self, *, label: str) -> dict[str, object]:
        previous_sequence = self._anchor_sequence
        previous_head = self._anchor_head_sha256
        sequence = previous_sequence + 1
        head = _hash(f"semantic-anchor-head:{label}:{sequence}:{previous_head}")
        result = {
            "previous_sequence": previous_sequence,
            "previous_head_sha256": previous_head,
            "sequence": sequence,
            "head_sha256": head,
            "commitment_sha256": _hash(
                f"semantic-anchor-commitment:{label}:{sequence}"
            ),
            "attestation_sha256": _hash(
                f"semantic-anchor-attestation:{label}:{sequence}"
            ),
            "local_previous_record_sha256": (
                "0" * 64
                if previous_sequence == 0
                else _hash(f"semantic-local-previous:{label}:{sequence}")
            ),
            "local_event_sha256": _hash(f"semantic-local-event:{label}:{sequence}"),
            "occurred_at": NOW,
        }
        self._anchor_sequence = sequence
        self._anchor_head_sha256 = head
        return result

    def read_receipts(self, *, run_id: UUID):
        del run_id
        return tuple(self.receipts)

    def claim_phase(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        sequence: int,
        phase_request_sha256: str,
        effect_key: str,
    ):
        for raw in self.receipts:
            receipt = driver.parse_physical_full_matrix_v4_run_receipt(raw)
            if (
                receipt.run_id == run_id
                and receipt.plan_sha256 == plan_sha256
                and receipt.sequence == sequence
                and receipt.phase_request_sha256 == phase_request_sha256
            ):
                return driver.PhysicalFullMatrixV4PhaseClaim(
                    run_id=run_id,
                    plan_sha256=plan_sha256,
                    sequence=sequence,
                    phase_request_sha256=phase_request_sha256,
                    effect_key=effect_key,
                    existing_receipt=raw,
                )
        key = (run_id, plan_sha256, sequence, phase_request_sha256)
        if key in self.effect_starts:
            return driver.PhysicalFullMatrixV4PhaseClaim(
                run_id=run_id,
                plan_sha256=plan_sha256,
                sequence=sequence,
                phase_request_sha256=phase_request_sha256,
                effect_key=effect_key,
                indeterminate=True,
            )
        if key in self.claims:
            # No effect-start record exists, so this local model permits the
            # root journal to reissue the unfinished claim safely.
            return driver.PhysicalFullMatrixV4PhaseClaim(
                run_id=run_id,
                plan_sha256=plan_sha256,
                sequence=sequence,
                phase_request_sha256=phase_request_sha256,
                effect_key=effect_key,
                claim_id=f"claim-v4-phase-{sequence:08d}",
            )
        self.claims.add(key)
        return driver.PhysicalFullMatrixV4PhaseClaim(
            run_id=run_id,
            plan_sha256=plan_sha256,
            sequence=sequence,
            phase_request_sha256=phase_request_sha256,
            effect_key=effect_key,
            claim_id=f"claim-v4-phase-{sequence:08d}",
        )

    def mark_effect_started(self, *, claim, effect_key: str):
        key = (
            claim.run_id,
            claim.plan_sha256,
            claim.sequence,
            claim.phase_request_sha256,
        )
        if key not in self.claims or claim.claim_id is None or effect_key != claim.effect_key:
            raise AssertionError("effect start is not live")
        result = driver.PhysicalFullMatrixV4EffectStart(
            run_id=claim.run_id,
            plan_sha256=claim.plan_sha256,
            sequence=claim.sequence,
            phase_request_sha256=claim.phase_request_sha256,
            effect_key=effect_key,
            claim_id=claim.claim_id,
        )
        self.effect_starts[key] = result
        self._start_anchors[key] = self._advance_anchor(
            label=f"effect-started:{result.claim_id}"
        )
        self.events.append("effect-started")
        return result

    def project_effect_start_anchor_proof(self, *, effect_start, request):
        key = (
            effect_start.run_id,
            effect_start.plan_sha256,
            effect_start.sequence,
            effect_start.phase_request_sha256,
        )
        if self.effect_starts.get(key) is not effect_start:
            raise AssertionError("effect start is not live")
        anchor = self._start_anchors[key]
        return driver._mint_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request,
            effect_start=effect_start,
            journal_binding_sha256=_hash("semantic-journal-binding"),
            baseline_plan_binding_sha256=_hash("semantic-baseline-binding"),
            anchor_genesis_sequence=0,
            anchor_genesis_head_sha256="0" * 64,
            anchor_previous_sequence=anchor["previous_sequence"],
            anchor_previous_head_sha256=anchor["previous_head_sha256"],
            anchor_sequence=anchor["sequence"],
            anchor_head_sha256=anchor["head_sha256"],
            anchor_commitment_sha256=anchor["commitment_sha256"],
            anchor_attestation_sha256=anchor["attestation_sha256"],
            anchor_local_previous_record_sha256=anchor[
                "local_previous_record_sha256"
            ],
            anchor_local_event_sha256=anchor["local_event_sha256"],
            anchor_occurred_at=anchor["occurred_at"],
        )

    def project_predecessor_phase_completion_anchor_proof(
        self, *, effect_start, request
    ):
        if effect_start.sequence <= 1:
            raise AssertionError("phase one has no predecessor completion")
        predecessor_key = next(
            (
                key
                for key, start in self.effect_starts.items()
                if (
                    start.run_id == effect_start.run_id
                    and start.plan_sha256 == effect_start.plan_sha256
                    and start.sequence == effect_start.sequence - 1
                )
            ),
            None,
        )
        if predecessor_key is None:
            raise AssertionError("predecessor effect start is missing")
        predecessor_start = self.effect_starts[predecessor_key]
        predecessor_start_anchor = self._start_anchors[predecessor_key]
        predecessor_completion_anchor = self._completion_anchors.get(predecessor_key)
        if predecessor_completion_anchor is None:
            raise AssertionError("predecessor completion is missing")
        return driver._mint_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
            request=request,
            predecessor_effect_start=predecessor_start,
            journal_binding_sha256=_hash("semantic-journal-binding"),
            baseline_plan_binding_sha256=_hash("semantic-baseline-binding"),
            anchor_genesis_sequence=0,
            anchor_genesis_head_sha256="0" * 64,
            predecessor_effect_start_anchor_previous_sequence=(
                predecessor_start_anchor["previous_sequence"]
            ),
            predecessor_effect_start_anchor_previous_head_sha256=(
                predecessor_start_anchor["previous_head_sha256"]
            ),
            predecessor_effect_start_anchor_sequence=predecessor_start_anchor["sequence"],
            predecessor_effect_start_anchor_head_sha256=(
                predecessor_start_anchor["head_sha256"]
            ),
            predecessor_effect_start_anchor_commitment_sha256=(
                predecessor_start_anchor["commitment_sha256"]
            ),
            predecessor_effect_start_anchor_attestation_sha256=(
                predecessor_start_anchor["attestation_sha256"]
            ),
            predecessor_effect_start_anchor_local_previous_record_sha256=(
                predecessor_start_anchor["local_previous_record_sha256"]
            ),
            predecessor_effect_start_anchor_local_event_sha256=(
                predecessor_start_anchor["local_event_sha256"]
            ),
            predecessor_effect_started_at=predecessor_start_anchor["occurred_at"],
            predecessor_completion_receipt_sha256=(
                driver.parse_physical_full_matrix_v4_run_receipt(
                    self.receipts[effect_start.sequence - 2]
                ).receipt_sha256
            ),
            predecessor_completion_anchor_previous_sequence=(
                predecessor_completion_anchor["previous_sequence"]
            ),
            predecessor_completion_anchor_previous_head_sha256=(
                predecessor_completion_anchor["previous_head_sha256"]
            ),
            predecessor_completion_anchor_sequence=(
                predecessor_completion_anchor["sequence"]
            ),
            predecessor_completion_anchor_head_sha256=(
                predecessor_completion_anchor["head_sha256"]
            ),
            predecessor_completion_anchor_commitment_sha256=(
                predecessor_completion_anchor["commitment_sha256"]
            ),
            predecessor_completion_anchor_attestation_sha256=(
                predecessor_completion_anchor["attestation_sha256"]
            ),
            predecessor_completion_anchor_local_previous_record_sha256=(
                predecessor_completion_anchor["local_previous_record_sha256"]
            ),
            predecessor_completion_anchor_local_event_sha256=(
                predecessor_completion_anchor["local_event_sha256"]
            ),
            predecessor_completed_at=predecessor_completion_anchor["occurred_at"],
        )

    def append_started(self, *, effect_start, canonical_receipt: bytes):
        key = (
            effect_start.run_id,
            effect_start.plan_sha256,
            effect_start.sequence,
            effect_start.phase_request_sha256,
        )
        if self.effect_starts.get(key) != effect_start or canonical_receipt in self.receipts:
            raise AssertionError("append-only claim invalid")
        parsed = driver.parse_physical_full_matrix_v4_run_receipt(canonical_receipt)
        if parsed.effect_key != effect_start.effect_key:
            raise AssertionError("effect key is not bound to receipt")
        self.receipts.append(canonical_receipt)
        self._completion_anchors[key] = self._advance_anchor(
            label=f"completed:{effect_start.claim_id}"
        )
        self.events.append("completed")
        return canonical_receipt


class _RaceJournal(_Journal):
    """Returns a stale first read after a phase was already receipted."""

    def __init__(self) -> None:
        super().__init__()
        self.hide_next_read = False

    def read_receipts(self, *, run_id: UUID):
        if self.hide_next_read:
            self.hide_next_read = False
            return ()
        return super().read_receipts(run_id=run_id)


class _TransitionRaceJournal(_Journal):
    """Makes a post-transition journal look one receipt behind once."""

    def __init__(self) -> None:
        super().__init__()
        self.stale_once = False

    def read_receipts(self, *, run_id: UUID):
        if self.stale_once:
            self.stale_once = False
            return tuple(self.receipts[:3])
        return super().read_receipts(run_id=run_id)


class _ClockRegressingJournal(_Journal):
    """Inject one backward clock movement from a journal read callback."""

    def __init__(self, *, clock: "_Clock", regress_on_read: int) -> None:
        super().__init__()
        self.clock = clock
        self.regress_on_read = regress_on_read
        self.read_count = 0

    def read_receipts(self, *, run_id: UUID):
        result = super().read_receipts(run_id=run_id)
        self.read_count += 1
        if self.read_count == self.regress_on_read:
            self.clock.now -= timedelta(seconds=1)
        return result


class _PostEffectCompletion:
    """Test-only opaque capability issued by one phase owner verifier."""

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("TEST_POST_EFFECT_COMPLETION_SERIALIZATION_FORBIDDEN")


class _PostEffectVerifier:
    """Independent fake owner that accepts only its own exact capability."""

    def __init__(self, phase: driver.PhysicalFullMatrixV4ExecutionPhase) -> None:
        self.phase_name = phase.name
        self.phase_sequence = phase.sequence
        self.oracle = phase.oracle
        self.transport_profile = phase.transport_profile
        self._issued: dict[
            int,
            tuple[
                _PostEffectCompletion,
                driver.PhysicalFullMatrixV4ExecutionRequest,
                driver.PhysicalFullMatrixV4EffectStartAuthority,
                driver.PhysicalFullMatrixV4EffectStartAnchorProof,
                str,
                datetime,
            ],
        ] = {}
        self.calls: list[tuple[object, datetime]] = []
        self.fail_on_call: int | None = None

    def issue(
        self,
        *,
        request: driver.PhysicalFullMatrixV4ExecutionRequest,
        authority: driver.PhysicalFullMatrixV4EffectStartAuthority,
        anchor: driver.PhysicalFullMatrixV4EffectStartAnchorProof,
        evidence_sha256: str,
        observed_at: datetime,
    ) -> _PostEffectCompletion:
        completion = _PostEffectCompletion()
        self._issued[id(completion)] = (
            completion,
            request,
            authority,
            anchor,
            evidence_sha256,
            observed_at,
        )
        return completion

    def require_post_effect_completion(
        self,
        *,
        request: driver.PhysicalFullMatrixV4ExecutionRequest,
        effect_start_authority: driver.PhysicalFullMatrixV4EffectStartAuthority,
        effect_start_anchor_proof: driver.PhysicalFullMatrixV4EffectStartAnchorProof,
        oracle: driver.PhysicalFullMatrixV4PhaseOracle,
        completion: object,
        observed_at: datetime,
        now: datetime,
        maximum_oracle_age_seconds: int,
    ) -> None:
        self.calls.append((completion, now))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise AssertionError("injected owner completion-verification failure")
        issued = self._issued.get(id(completion))
        if issued is None:
            raise AssertionError("foreign post-effect completion")
        (
            issued_completion,
            issued_request,
            issued_authority,
            issued_anchor,
            issued_evidence_sha256,
            issued_observed_at,
        ) = issued
        if (
            completion is not issued_completion
            or request is not issued_request
            or effect_start_authority is not issued_authority
            or effect_start_anchor_proof is not issued_anchor
            or oracle.phase != self.phase_name
            or oracle.oracle != self.oracle
            or oracle.transport_profile != self.transport_profile
            or oracle.evidence_sha256 != issued_evidence_sha256
            or observed_at != issued_observed_at
            or oracle.observed_at != issued_observed_at
            or now - observed_at
            > timedelta(seconds=maximum_oracle_age_seconds)
            or observed_at > now + timedelta(seconds=5)
        ):
            raise AssertionError("post-effect completion correlation mismatch")
        if (
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=request
            )
            is not effect_start_authority
            or driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=request
            )
            is not effect_start_anchor_proof
        ):
            raise AssertionError("post-effect completion private correlation mismatch")


class _Adapter:
    def __init__(self, now: datetime, *, verifier: _PostEffectVerifier) -> None:
        self.now = now
        self.verifier = verifier
        self.requests: list[driver.PhysicalFullMatrixV4ExecutionRequest] = []
        self.effect_start_authorities: list[
            driver.PhysicalFullMatrixV4EffectStartAuthority
        ] = []
        self.effect_start_anchor_proofs: list[
            driver.PhysicalFullMatrixV4EffectStartAnchorProof
        ] = []
        self.predecessor_completion_anchor_proofs: list[
            driver.PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof
        ] = []
        self.emit_successor = True
        self.tamper_direct_control = False
        self.before_execute: Callable[[], None] | None = None
        self.after_execute: Callable[[], None] | None = None
        self.raise_after_effect = False
        self.emit_post_effect_completion = True

    def execute_phase(self, *, request: driver.PhysicalFullMatrixV4ExecutionRequest):
        self.effect_start_authorities.append(
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=request
            )
        )
        self.effect_start_anchor_proofs.append(
            driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=request
            )
        )
        if request.phase.sequence > 1:
            self.predecessor_completion_anchor_proofs.append(
                driver.require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
                    request=request
                )
            )
        if self.before_execute is not None:
            self.before_execute()
        self.requests.append(request)
        successor = None
        if (
            self.emit_successor
            and request.phase.name
            in {"witness-promote-ir-v2", "witness-restore-fi-writer-v2"}
        ):
            successor = _evidence(_successor(request.binding))
        evidence_sha256 = _hash(f"oracle-{request.phase.sequence}")
        completion = None
        if self.emit_post_effect_completion:
            completion = self.verifier.issue(
                request=request,
                authority=self.effect_start_authorities[-1],
                anchor=self.effect_start_anchor_proofs[-1],
                evidence_sha256=evidence_sha256,
                observed_at=self.now,
            )
        result = driver.PhysicalFullMatrixV4PhaseOracle(
            schema=driver.PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA,
            status="oracle-succeeded",
            phase=request.phase.name,
            oracle=request.phase.oracle,
            transport_profile=request.phase.transport_profile,
            effect_key=request.effect_key,
            evidence_sha256=evidence_sha256,
            observed_at=self.now,
            readiness_evidence=request.pre_effect_readiness_evidence,
            direct_ir_to_fi_control=(
                "permitted" if self.tamper_direct_control else "forbidden"
            ),
            successor_readiness_evidence=successor,
            post_effect_completion=completion,
        )
        if self.after_execute is not None:
            self.after_execute()
        if self.raise_after_effect:
            raise RuntimeError("simulated crash after effect")
        return result


class _Resolver:
    """Root-owned lookup model; it returns no historical capability by default."""

    def __init__(self) -> None:
        self.mode = "fresh"
        self.calls: list[driver.PhysicalFullMatrixV4ExecutionBinding] = []
        self.rejected_binding_sha256: set[str] = set()

    def resolve_readiness(self, *, binding: driver.PhysicalFullMatrixV4ExecutionBinding):
        self.calls.append(binding)
        if binding.readiness_binding_sha256 in self.rejected_binding_sha256:
            return None
        if self.mode == "missing":
            return None
        if self.mode == "mismatched":
            return _evidence(_successor(binding))
        if self.mode == "blocked":
            return driver.PhysicalFullMatrixV4ReadinessEvidence(
                binding=binding,
                readiness=VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness(
                    report=replace(_report_for(binding), status="blocked")
                ),
            )
        return _evidence(binding)


class _ClockAdvancingResolver(_Resolver):
    """Models a resolver that blocks until its answer has expired.

    The driver must sample its trusted clock *after* this callback.  Advancing
    the clock on a selected resolution lets the tests distinguish that fence
    from a pre-callback-only validation.
    """

    def __init__(self, *, clock: "_Clock", advance_on_call: int, advance: timedelta) -> None:
        super().__init__()
        self.clock = clock
        self.advance_on_call = advance_on_call
        self.advance = advance
        self.resolution_calls = 0

    def resolve_readiness(self, *, binding: driver.PhysicalFullMatrixV4ExecutionBinding):
        evidence = super().resolve_readiness(binding=binding)
        self.resolution_calls += 1
        if self.resolution_calls == self.advance_on_call:
            self.clock.now += self.advance
        return evidence


class _ClockRegressingResolver(_Resolver):
    """Inject one backward clock movement from a resolver callback."""

    def __init__(self, *, clock: "_Clock", regress_on_call: int) -> None:
        super().__init__()
        self.clock = clock
        self.regress_on_call = regress_on_call
        self.resolution_calls = 0

    def resolve_readiness(self, *, binding: driver.PhysicalFullMatrixV4ExecutionBinding):
        evidence = super().resolve_readiness(binding=binding)
        self.resolution_calls += 1
        if self.resolution_calls == self.regress_on_call:
            self.clock.now -= timedelta(seconds=1)
        return evidence


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.calls = 0

    def now_utc(self) -> datetime:
        self.calls += 1
        return self.now


class _CampaignContinuityGate:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str, int, driver.PhysicalFullMatrixV4ExecutionBinding]] = []
        self.fail = False

    def verify_campaign_continuity(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        completed_sequence: int,
        active_binding: driver.PhysicalFullMatrixV4ExecutionBinding,
    ) -> None:
        if self.fail:
            raise RuntimeError("root campaign anchor unavailable")
        self.calls.append((run_id, plan_sha256, completed_sequence, active_binding))


class _ClockRegressingCampaignContinuityGate(_CampaignContinuityGate):
    """Inject one backward clock movement from the continuity callback."""

    def __init__(self, *, clock: _Clock, regress_on_call: int) -> None:
        super().__init__()
        self.clock = clock
        self.regress_on_call = regress_on_call

    def verify_campaign_continuity(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        completed_sequence: int,
        active_binding: driver.PhysicalFullMatrixV4ExecutionBinding,
    ) -> None:
        super().verify_campaign_continuity(
            run_id=run_id,
            plan_sha256=plan_sha256,
            completed_sequence=completed_sequence,
            active_binding=active_binding,
        )
        if len(self.calls) == self.regress_on_call:
            self.clock.now -= timedelta(seconds=1)


class _FaultInjectingJournal(_Journal):
    """Local callback-fault model for one V4 driver invocation.

    The model deliberately distinguishes a callback that fails *before* it
    records anything from one that loses its response after durable state was
    recorded.  It never represents a provider, host, storage client, or real
    phase effect; the tests use it only to prove the driver's fail-closed
    control flow.
    """

    def __init__(
        self,
        *,
        fail_read_calls: set[int] | None = None,
        empty_read_calls: set[int] | None = None,
        fail_claim_calls: set[int] | None = None,
        fail_effect_start_calls: set[int] | None = None,
        fail_append_before_calls: set[int] | None = None,
        fail_append_after_calls: set[int] | None = None,
    ) -> None:
        super().__init__()
        self.fail_read_calls = set() if fail_read_calls is None else set(fail_read_calls)
        self.empty_read_calls = set() if empty_read_calls is None else set(empty_read_calls)
        self.fail_claim_calls = set() if fail_claim_calls is None else set(fail_claim_calls)
        self.fail_effect_start_calls = (
            set() if fail_effect_start_calls is None else set(fail_effect_start_calls)
        )
        self.fail_append_before_calls = (
            set()
            if fail_append_before_calls is None
            else set(fail_append_before_calls)
        )
        self.fail_append_after_calls = (
            set() if fail_append_after_calls is None else set(fail_append_after_calls)
        )
        self.read_calls = 0
        self.claim_calls = 0
        self.effect_start_calls = 0
        self.append_calls = 0

    def read_receipts(self, *, run_id: UUID):
        self.read_calls += 1
        if self.read_calls in self.fail_read_calls:
            raise RuntimeError("injected durable-read callback failure")
        if self.read_calls in self.empty_read_calls:
            return ()
        return super().read_receipts(run_id=run_id)

    def claim_phase(self, **kwargs):
        self.claim_calls += 1
        if self.claim_calls in self.fail_claim_calls:
            raise RuntimeError("injected claim callback failure")
        return super().claim_phase(**kwargs)

    def mark_effect_started(self, **kwargs):
        self.effect_start_calls += 1
        if self.effect_start_calls in self.fail_effect_start_calls:
            raise RuntimeError("injected effect-start callback failure")
        return super().mark_effect_started(**kwargs)

    def append_started(self, **kwargs):
        self.append_calls += 1
        if self.append_calls in self.fail_append_before_calls:
            raise RuntimeError("injected append callback failure before durable record")
        result = super().append_started(**kwargs)
        if self.append_calls in self.fail_append_after_calls:
            raise RuntimeError("injected append response loss after durable record")
        return result


class _FaultInjectingResolver(_Resolver):
    """Raise at a selected resolver callback without returning evidence."""

    def __init__(self, *, fail_calls: set[int]) -> None:
        super().__init__()
        self.fail_calls = set(fail_calls)
        self.invocations = 0

    def resolve_readiness(self, *, binding: driver.PhysicalFullMatrixV4ExecutionBinding):
        self.invocations += 1
        if self.invocations in self.fail_calls:
            raise RuntimeError("injected readiness callback failure")
        return super().resolve_readiness(binding=binding)


class _FaultInjectingCampaignContinuityGate(_CampaignContinuityGate):
    """Raise at a selected durable-continuity callback."""

    def __init__(self, *, fail_calls: set[int]) -> None:
        super().__init__()
        self.fail_calls = set(fail_calls)
        self.invocations = 0

    def verify_campaign_continuity(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        completed_sequence: int,
        active_binding: driver.PhysicalFullMatrixV4ExecutionBinding,
    ) -> None:
        self.invocations += 1
        if self.invocations in self.fail_calls:
            raise RuntimeError("injected continuity callback failure")
        super().verify_campaign_continuity(
            run_id=run_id,
            plan_sha256=plan_sha256,
            completed_sequence=completed_sequence,
            active_binding=active_binding,
        )


class PhysicalFullMatrixV4ExecutionDriverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = _binding()
        self.readiness = _opaque_readiness(self.binding)
        self.config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=self.binding,
            readiness=self.readiness,
            run_id=UUID("7ea994a3-a50a-4f10-bdaf-c75278a0ea74"),
            enabled=True,
        )
        self._readiness_verifier = patch.object(
            driver,
            "require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
            side_effect=lambda item, *, now=None: item.report,
        )
        self._readiness_verifier.start()
        self.addCleanup(self._readiness_verifier.stop)

    def plan(self, **changes: object) -> driver.PhysicalFullMatrixV4ExecutionPlan:
        return driver.build_physical_full_matrix_v4_execution_plan(
            config=replace(self.config, **changes)
        )

    def adapters(
        self,
        *,
        now: datetime = NOW,
        journal: _Journal | None = None,
    ) -> tuple[driver.PhysicalFullMatrixV4ExecutionAdapters, _Journal, dict[str, _Adapter]]:
        verifiers = {
            phase.name: _PostEffectVerifier(phase)
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        mapped = {
            phase.name: _Adapter(now, verifier=verifiers[phase.name])
            for phase in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        }
        root_journal = _Journal() if journal is None else journal
        return (
            driver.PhysicalFullMatrixV4ExecutionAdapters(
                phase_adapters=mapped,
                receipt_journal=root_journal,
                readiness_resolver=_Resolver(),
                trusted_clock=_Clock(NOW),
                campaign_continuity_gate=_CampaignContinuityGate(),
                phase_post_effect_verifiers=verifiers,
            ),
            root_journal,
            mapped,
        )

    def test_v4_catalog_and_plan_are_default_off_and_non_authorizing(self) -> None:
        self.assertEqual(8, len(driver.PHYSICAL_FULL_MATRIX_V4_PHASES))
        self.assertEqual(
            "fi-v2-witness-roundtrip-strict-ack-v1",
            driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0].transport_profile,
        )
        self.assertEqual(
            "ir-v2-witness-roundtrip-strict-ack-v1",
            driver.PHYSICAL_FULL_MATRIX_V4_PHASES[4].transport_profile,
        )
        with self.assertRaisesRegex(driver.PhysicalFullMatrixV4ExecutionDriverError, "DISABLED"):
            self.plan(enabled=False)

        plan = self.plan()
        self.assertFalse(plan.materialization_authorized)
        self.assertFalse(plan.promotion_authorized)
        self.assertFalse(plan.execution_authorized)
        self.assertIn(b'"execution_authorized":false', plan.canonical_plan)
        self.assertIn(b'"direct_fi_to_ir_control":"forbidden"', plan.canonical_plan)
        self.assertIn(b'"legacy_runner_compatibility":"forbidden"', plan.canonical_plan)
        self.assertIs(plan, driver.require_physical_full_matrix_v4_execution_plan(plan))

        adapters, _journal, _mapped = self.adapters()
        for field_name, code in (
            ("readiness_resolver", "READINESS_RESOLVER_MISSING"),
            ("trusted_clock", "TRUSTED_CLOCK_MISSING"),
            ("campaign_continuity_gate", "CAMPAIGN_CONTINUITY_GATE_MISSING"),
        ):
            with self.subTest(field_name=field_name), self.assertRaisesRegex(
                driver.PhysicalFullMatrixV4ExecutionDriverError, code
            ):
                driver.prepare_physical_full_matrix_v4_execution_adapters(
                    plan=plan,
                    adapters=replace(adapters, **{field_name: None}),
                )

    def test_execution_requires_exact_post_effect_verifier_map_before_claim_or_start(self) -> None:
        """The planning helper stays non-operational; execute does not."""

        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        without_verifiers = replace(adapters, phase_post_effect_verifiers=None)
        # Default-off composition/prepare remains able to inspect the normal
        # adapter surface without inventing a live phase-completion owner.
        driver.prepare_physical_full_matrix_v4_execution_adapters(
            plan=plan,
            adapters=without_verifiers,
        )
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHASE_POST_EFFECT_VERIFIER_MAP_REQUIRED",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config,
                plan=plan,
                adapters=without_verifiers,
                now=NOW,
            )
        self.assertEqual([], journal.events)
        self.assertEqual({}, journal.effect_starts)
        self.assertEqual([], journal.receipts)
        self.assertTrue(all(not adapter.requests for adapter in mapped.values()))

        assert adapters.phase_post_effect_verifiers is not None
        wrong_phase_map = dict(adapters.phase_post_effect_verifiers)
        wrong_phase_map[plan.phases[0].name] = wrong_phase_map[plan.phases[1].name]
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHASE_POST_EFFECT_VERIFIER_BINDING_MISMATCH",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config,
                plan=plan,
                adapters=replace(
                    adapters,
                    phase_post_effect_verifiers=wrong_phase_map,
                ),
                now=NOW,
            )
        self.assertEqual([], journal.events)
        self.assertEqual({}, journal.effect_starts)
        self.assertEqual([], journal.receipts)

    def test_plain_success_oracle_cannot_complete_a_phase_without_owner_capability(self) -> None:
        """A post-callback verifier failure leaves the durable start unresolved."""

        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        phase_name = plan.phases[0].name
        adapter = mapped[phase_name]
        adapter.emit_post_effect_completion = False
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHASE_POST_EFFECT_COMPLETION_REQUIRED",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config,
                plan=plan,
                adapters=adapters,
                now=NOW,
            )
        self.assertEqual(1, len(adapter.requests))
        self.assertEqual(1, len(journal.effect_starts))
        self.assertEqual([], journal.receipts)
        self.assertEqual(["effect-started"], journal.events)

        # The effect was started but never proved complete, so it can never
        # be automatically retried by a later execute call.
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "EFFECT_INDETERMINATE",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config,
                plan=plan,
                adapters=adapters,
                now=NOW,
            )
        self.assertEqual(1, len(adapter.requests))
        self.assertEqual([], journal.receipts)

    def test_post_effect_completion_is_reverified_immediately_before_receipt_append(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        phase_name = plan.phases[0].name
        assert adapters.phase_post_effect_verifiers is not None
        verifier = adapters.phase_post_effect_verifiers[phase_name]
        assert isinstance(verifier, _PostEffectVerifier)
        verifier.fail_on_call = 2
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHASE_POST_EFFECT_COMPLETION_INVALID",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config,
                plan=plan,
                adapters=adapters,
                now=NOW,
            )
        self.assertEqual(1, len(mapped[phase_name].requests))
        self.assertEqual(2, len(verifier.calls))
        self.assertEqual(1, len(journal.effect_starts))
        self.assertEqual([], journal.receipts)
        self.assertEqual(["effect-started"], journal.events)

    def test_only_opaque_gen2_readiness_and_no_legacy_runner_are_accepted(self) -> None:
        with self.assertRaisesRegex(driver.PhysicalFullMatrixV4ExecutionDriverError, "LEGACY"):
            self.plan(legacy_runner_artifacts=("v1-runner",))
        with self.assertRaisesRegex(driver.PhysicalFullMatrixV4ExecutionDriverError, "PROVENANCE"):
            self.plan(readiness=_report_for(self.binding))

        self._readiness_verifier.stop()
        forged = VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness(
            report=_report_for(self.binding)
        )
        with self.assertRaisesRegex(driver.PhysicalFullMatrixV4ExecutionDriverError, "PROVENANCE"):
            driver.build_physical_full_matrix_v4_execution_plan(
                config=replace(self.config, readiness=forged)
            )

    def test_real_owner_minted_gen1_readiness_is_explicitly_rejected(self) -> None:
        """A genuine historical Gen1 capability has no V4 adapter/fallback."""

        self._readiness_verifier.stop()
        case_type = gen1_readiness_tests.PhysicalFullMatrixV2WitnessedCampaignReadinessTests
        case_type.setUpClass()
        case = case_type("runTest")
        try:
            with case._bridge_owner_context():
                verified = gen1_readiness_owner.mint_verified_physical_full_matrix_v2_witnessed_campaign_readiness(
                    config=case.config,
                    inputs=case.inputs,
                    now=gen1_readiness_tests.NOW,
                )
            self.assertIs(
                type(verified),
                VerifiedPhysicalFullMatrixV2WitnessedCampaignReadiness,
            )
            source = case.binding
            historical_binding = driver.PhysicalFullMatrixV4ExecutionBinding(
                campaign_id=source.campaign_id,
                release_sha=source.release_sha,
                readiness_binding_sha256=verified.report.binding_sha256,
                route_commitment_sha256=source.route_commitment_sha256,
                four_role_binding_sha256=source.four_role_binding_sha256,
                writer_holder_site=source.writer_holder_site,
                writer_epoch=source.writer_epoch,
                writer_lease_id=source.writer_lease_id,
                witnessed_term_proof_sha256=source.witnessed_term_proof_sha256,
                source_site=source.source_site,
                destination_site=source.destination_site,
                roundtrip_attestation_sha256=source.roundtrip_attestation_sha256,
                roundtrip_configuration_sha256=source.roundtrip_configuration_sha256,
                witness_transition_id=source.witness_transition_id,
                witness_sequence=source.witness_sequence,
            )
            with self.assertRaisesRegex(
                driver.PhysicalFullMatrixV4ExecutionDriverError,
                "PHYSICAL_FULL_MATRIX_V4_READINESS_PROVENANCE_INVALID",
            ):
                driver.build_physical_full_matrix_v4_execution_plan(
                    config=driver.PhysicalFullMatrixV4ExecutionConfig(
                        binding=historical_binding,
                        readiness=verified,  # type: ignore[arg-type]
                        run_id=UUID("aea994a3-a50a-4f10-bdaf-c75278a0ea74"),
                        enabled=True,
                    )
                )
        finally:
            case_type.tearDownClass()
            self._readiness_verifier.start()

    def test_real_owner_minted_gen2_readiness_builds_only_a_non_authorizing_plan(self) -> None:
        """V4 accepts a fresh real Gen2 owner capability without an adapter."""

        self._readiness_verifier.stop()
        fixture = Gen2WitnessedAckChainFixture()
        fixture.setUp()
        try:
            # The real Gen2 fixture has its own canonical test clock.  Keep the
            # complete witnessed chain and the readiness owner on that one clock:
            # V4's module-local NOW intentionally belongs only to its fake-driver
            # cases and would make the short-lived upstream evidence stale.
            chain = fixture.mint_chain(now=fixture.now)
            readiness_binding = (
                gen2_readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding(
                    **{
                        item.name: getattr(chain, item.name)
                        for item in fields(
                            gen2_readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignBinding
                        )
                    }
                )
            )
            readiness_config = (
                gen2_readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig(
                    binding=readiness_binding,
                    gen2_witnessed_ack_chain_config=fixture.config,
                    enabled=True,
                )
            )
            readiness_inputs = (
                gen2_readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignInputs(
                    gen2_witnessed_ack_chain=chain,
                )
            )
            with fixture._all_owner_clocks(now=fixture.now):
                verified = (
                    gen2_readiness_owner.mint_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
                        config=readiness_config,
                        inputs=readiness_inputs,
                        now=fixture.now,
                    )
                )
                real_binding = driver.PhysicalFullMatrixV4ExecutionBinding(
                    campaign_id=readiness_binding.campaign_id,
                    release_sha=readiness_binding.release_sha,
                    readiness_binding_sha256=verified.report.binding_sha256,
                    route_commitment_sha256=readiness_binding.route_commitment_sha256,
                    four_role_binding_sha256=readiness_binding.four_role_binding_sha256,
                    writer_holder_site=readiness_binding.writer_holder_site,
                    writer_epoch=readiness_binding.writer_epoch,
                    writer_lease_id=readiness_binding.writer_lease_id,
                    witnessed_term_proof_sha256=readiness_binding.witnessed_term_proof_sha256,
                    source_site=readiness_binding.source_site,
                    destination_site=readiness_binding.destination_site,
                    roundtrip_attestation_sha256=(
                        readiness_binding.roundtrip_attestation_sha256
                    ),
                    roundtrip_configuration_sha256=(
                        readiness_binding.roundtrip_configuration_sha256
                    ),
                    witness_transition_id=readiness_binding.witness_transition_id,
                    witness_sequence=readiness_binding.witness_sequence,
                )
                plan = driver.build_physical_full_matrix_v4_execution_plan(
                    config=driver.PhysicalFullMatrixV4ExecutionConfig(
                        binding=real_binding,
                        readiness=verified,
                        run_id=UUID("bea994a3-a50a-4f10-bdaf-c75278a0ea74"),
                        enabled=True,
                    )
                )
        finally:
            fixture.tearDown()
            self._readiness_verifier.start()
        self.assertFalse(plan.execution_authorized)
        self.assertFalse(plan.promotion_authorized)
        self.assertFalse(plan.materialization_authorized)
        self.assertEqual(real_binding, plan.binding)

    def test_all_phases_switch_direction_only_after_fresh_successors(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        results = [
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
            for _ in driver.PHYSICAL_FULL_MATRIX_V4_PHASES
        ]
        self.assertEqual(8, len(journal.receipts))
        self.assertTrue(all(item.full_matrix_executed is False for item in results))
        self.assertEqual(
            ("webapp_fi", "webapp_ir"),
            (
                mapped["recover-ir-through-object-storage-v2"].requests[0].binding.source_site,
                mapped["recover-ir-through-object-storage-v2"].requests[0].binding.destination_site,
            ),
        )
        self.assertEqual(
            ("webapp_ir", "webapp_fi"),
            (
                mapped["ir-writer-v2-witness-roundtrip-strict-ack-matrix"].requests[0].binding.source_site,
                mapped["ir-writer-v2-witness-roundtrip-strict-ack-matrix"].requests[0].binding.destination_site,
            ),
        )
        self.assertEqual(
            ("webapp_fi", "webapp_ir"),
            (
                mapped["final-three-site-v2-convergence-oracle"].requests[0].binding.source_site,
                mapped["final-three-site-v2-convergence-oracle"].requests[0].binding.destination_site,
            ),
        )
        promotion = driver.parse_physical_full_matrix_v4_run_receipt(journal.receipts[3])
        restoration = driver.parse_physical_full_matrix_v4_run_receipt(journal.receipts[6])
        self.assertGreater(
            promotion.successor_binding.writer_epoch, self.binding.writer_epoch  # type: ignore[union-attr]
        )
        self.assertGreater(
            restoration.successor_binding.writer_epoch, promotion.successor_binding.writer_epoch  # type: ignore[union-attr]
        )
        bridges = [
            proof
            for adapter in mapped.values()
            for proof in adapter.predecessor_completion_anchor_proofs
        ]
        self.assertEqual(7, len(bridges))
        self.assertEqual(
            tuple(range(1, 8)),
            tuple(proof.predecessor_phase_sequence for proof in bridges),
        )
        self.assertEqual(
            tuple(range(2, 9)),
            tuple(proof.successor_phase_sequence for proof in bridges),
        )
        self.assertTrue(
            all(
                proof.predecessor_completion_anchor_sequence
                == proof.successor_effect_start_anchor_previous_sequence
                and proof.predecessor_completion_anchor_head_sha256
                == proof.successor_effect_start_anchor_previous_head_sha256
                for proof in bridges
            )
        )

    def test_transition_safe_owner_term_rollover_uses_successor_not_retired_normal_readiness(self) -> None:
        """Both writer switches may retire the old owner capability in place."""

        self._readiness_verifier.stop()
        retired_readiness_bindings: set[str] = set()

        def verify(item, *, now=None):
            del now
            if item.report.binding_sha256 in retired_readiness_bindings:
                raise gen2_readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError(
                    "retired-live-writer-term"
                )
            return item.report

        try:
            with patch.object(
                driver,
                "require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
                side_effect=verify,
            ):
                plan = driver.build_physical_full_matrix_v4_execution_plan(
                    config=self.config
                )
                adapters, journal, mapped = self.adapters()
                mapped["witness-promote-ir-v2"].after_execute = lambda: (
                    retired_readiness_bindings.add(self.binding.readiness_binding_sha256)
                )
                for _ in range(4):
                    driver.execute_next_physical_full_matrix_v4_phase(
                        config=self.config, plan=plan, adapters=adapters, now=NOW
                    )
                promoted = driver.parse_physical_full_matrix_v4_run_receipt(
                    journal.receipts[3]
                )
                assert promoted.successor_binding is not None
                mapped["witness-restore-fi-writer-v2"].after_execute = lambda: (
                    retired_readiness_bindings.add(
                        promoted.successor_binding.readiness_binding_sha256
                    )
                )
                for _ in range(4):
                    driver.execute_next_physical_full_matrix_v4_phase(
                        config=self.config, plan=plan, adapters=adapters, now=NOW
                    )
        finally:
            self._readiness_verifier.start()
        self.assertEqual(8, len(journal.receipts))
        self.assertEqual(
            1,
            len(mapped["ir-writer-v2-witness-roundtrip-strict-ack-matrix"].requests),
        )
        self.assertEqual(1, len(mapped["final-three-site-v2-convergence-oracle"].requests))

    def test_pre_and_post_transition_resolver_fences_fail_closed(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        resolver = adapters.readiness_resolver
        assert isinstance(resolver, _Resolver)
        resolver.mode = "blocked"
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "READINESS_INCOMPLETE",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)
        self.assertEqual([], mapped[plan.phases[0].name].requests)
        self.assertEqual({}, journal.effect_starts)

        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        resolver = adapters.readiness_resolver
        assert isinstance(resolver, _Resolver)
        for _ in range(3):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        mapped["witness-promote-ir-v2"].after_execute = lambda: setattr(
            resolver, "mode", "missing"
        )
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHASE_READINESS_REQUIRED",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual(3, len(journal.receipts))
        self.assertEqual(4, len(journal.effect_starts))
        self.assertEqual([], mapped["ir-writer-v2-witness-roundtrip-strict-ack-matrix"].requests)

        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        resolver = adapters.readiness_resolver
        assert isinstance(resolver, _Resolver)
        mapped[plan.phases[0].name].after_execute = lambda: setattr(
            resolver, "mode", "missing"
        )
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHASE_READINESS_REQUIRED",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)
        self.assertEqual(1, len(journal.effect_starts))

    def test_campaign_continuity_anchor_is_required_before_any_adapter(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        gate = adapters.campaign_continuity_gate
        assert isinstance(gate, _CampaignContinuityGate)
        gate.fail = True
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "CAMPAIGN_CONTINUITY_UNVERIFIED",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)
        self.assertTrue(all(not adapter.requests for adapter in mapped.values()))

    def test_transition_successors_are_required_and_never_precredited(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        mapped["witness-promote-ir-v2"].emit_successor = False
        for _ in range(3):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        with self.assertRaisesRegex(driver.PhysicalFullMatrixV4ExecutionDriverError, "SUCCESSOR_REQUIRED"):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual(3, len(journal.receipts))
        self.assertEqual(
            [], mapped["ir-writer-v2-witness-roundtrip-strict-ack-matrix"].requests
        )

        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        mapped["witness-restore-fi-writer-v2"].emit_successor = False
        for _ in range(6):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        with self.assertRaisesRegex(driver.PhysicalFullMatrixV4ExecutionDriverError, "SUCCESSOR_REQUIRED"):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual(6, len(journal.receipts))
        self.assertEqual([], mapped["final-three-site-v2-convergence-oracle"].requests)

    def test_restart_reobtains_exact_reverse_readiness_before_phase_five(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        for _ in range(4):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        resolver = adapters.readiness_resolver
        assert isinstance(resolver, _Resolver)
        for mode, expected in (
            ("missing", "PHASE_READINESS_REQUIRED"),
            ("mismatched", "DIRECTION_INVALID|PHASE_READINESS_MISMATCH"),
            ("blocked", "READINESS_INCOMPLETE"),
        ):
            with self.subTest(mode=mode):
                resolver.mode = mode
                with self.assertRaisesRegex(
                    driver.PhysicalFullMatrixV4ExecutionDriverError, expected
                ):
                    driver.execute_next_physical_full_matrix_v4_phase(
                        config=self.config, plan=plan, adapters=adapters, now=NOW
                    )
                self.assertEqual(
                    [], mapped["ir-writer-v2-witness-roundtrip-strict-ack-matrix"].requests
                )
                self.assertEqual(4, len(journal.receipts))

    def test_restart_reobtains_return_readiness_before_phase_eight(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        for _ in range(7):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        resolver = adapters.readiness_resolver
        assert isinstance(resolver, _Resolver)
        resolver.mode = "missing"
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHASE_READINESS_REQUIRED",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual(7, len(journal.receipts))
        self.assertEqual([], mapped["final-three-site-v2-convergence-oracle"].requests)

    def test_durable_effect_start_crash_is_indeterminate_and_never_retries(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        adapter = mapped[plan.phases[0].name]
        adapter.raise_after_effect = True
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError, "PHASE_ADAPTER_FAILED"
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual(1, len(adapter.requests))
        self.assertEqual(1, len(journal.effect_starts))
        self.assertEqual([], journal.receipts)

        adapter.raise_after_effect = False
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError, "EFFECT_INDETERMINATE"
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual(1, len(adapter.requests))
        self.assertEqual([], journal.receipts)

    def test_effect_start_authority_is_minted_after_journal_start_and_bound_to_adapter(self) -> None:
        """The adapter sees correlation only after the exact durable start."""

        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        adapter = mapped[plan.phases[0].name]
        snapshot = driver._snapshot(plan)
        pre_effect_request = driver._request(
            snapshot=snapshot,
            phase=snapshot.phases[0],
            binding=snapshot.binding,
        )
        self.assertIsNone(pre_effect_request._effect_start_authority)
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUIRED",
        ):
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=pre_effect_request
            )

        # The adapter callback is the first code that can observe the private
        # authority; the durable root-journal transition must already exist.
        adapter.before_execute = lambda: self.assertEqual(
            ["effect-started"], journal.events
        )
        driver.execute_next_physical_full_matrix_v4_phase(
            config=self.config,
            plan=plan,
            adapters=adapters,
            now=NOW,
        )

        self.assertEqual(["effect-started", "completed"], journal.events)
        self.assertEqual(1, len(adapter.requests))
        self.assertEqual(1, len(adapter.effect_start_authorities))
        request = adapter.requests[0]
        authority = adapter.effect_start_authorities[0]
        self.assertIs(
            authority,
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=request
            ),
        )
        start = next(iter(journal.effect_starts.values()))
        self.assertEqual(plan.run_id, authority.run_id)
        self.assertEqual(plan.plan_sha256, authority.plan_sha256)
        self.assertEqual(request.phase, authority.phase)
        self.assertEqual(request.effect_key, authority.effect_key)
        self.assertEqual(request.phase_request_sha256, authority.phase_request_sha256)
        self.assertEqual(request.binding, authority.binding)
        self.assertEqual(start.claim_id, authority.claim_id)
        self.assertEqual(
            driver._journaled_effect_start_identity(start),
            authority.journaled_effect_start_identity_sha256,
        )
        self.assertFalse(authority.writer_authorized)
        self.assertFalse(authority.promotion_authorized)
        self.assertFalse(authority.execution_authorized)
        self.assertFalse(authority.full_matrix_authorized)

        # The private adapter field cannot perturb either canonical request
        # hash, and a normal copy remains deliberately authority-free.
        self.assertEqual(pre_effect_request.effect_key, request.effect_key)
        self.assertEqual(
            pre_effect_request.phase_request_sha256,
            request.phase_request_sha256,
        )
        ordinary_copy = driver._request_copy(request)
        self.assertIsNone(ordinary_copy._effect_start_authority)
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUIRED",
        ):
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=ordinary_copy
            )

    def test_effect_start_authority_is_noncopyable_nonserializable_and_unforgeable(self) -> None:
        plan = self.plan()
        adapters, _journal, mapped = self.adapters()
        adapter = mapped[plan.phases[0].name]
        driver.execute_next_physical_full_matrix_v4_phase(
            config=self.config,
            plan=plan,
            adapters=adapters,
            now=NOW,
        )
        request = adapter.requests[0]
        authority = adapter.effect_start_authorities[0]

        for operation in (
            lambda: copy.copy(authority),
            lambda: copy.deepcopy(authority),
            lambda: pickle.dumps(authority),
        ):
            with self.assertRaisesRegex(TypeError, "EFFECT_START_AUTHORITY"):
                operation()

        with self.assertRaisesRegex(
            TypeError,
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_CONSTRUCTION_FORBIDDEN",
        ):
            driver.PhysicalFullMatrixV4EffectStartAuthority(
                run_id=authority.run_id,
                plan_sha256=authority.plan_sha256,
                phase=authority.phase,
                effect_key=authority.effect_key,
                phase_request_sha256=authority.phase_request_sha256,
                binding=authority.binding,
                claim_id=authority.claim_id,
                journaled_effect_start_identity_sha256=(
                    authority.journaled_effect_start_identity_sha256
                ),
                capability=object(),
            )

        # Even code that can name a module-private construction sentinel cannot
        # fabricate the root-owned live-state record used for correlation.
        forged = driver.PhysicalFullMatrixV4EffectStartAuthority(
            run_id=authority.run_id,
            plan_sha256=authority.plan_sha256,
            phase=authority.phase,
            effect_key=authority.effect_key,
            phase_request_sha256=authority.phase_request_sha256,
            binding=authority.binding,
            claim_id=authority.claim_id,
            journaled_effect_start_identity_sha256=(
                authority.journaled_effect_start_identity_sha256
            ),
            capability=driver._EFFECT_START_AUTHORITY_CAPABILITY,
        )
        forged_request = driver._request_copy(request)
        object.__setattr__(forged_request, "_effect_start_authority", forged)
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUIRED",
        ):
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=forged_request
            )

        snapshot = driver._snapshot(plan)
        mismatched_request = driver._request(
            snapshot=snapshot,
            phase=snapshot.phases[1],
            binding=snapshot.binding,
        )
        object.__setattr__(mismatched_request, "_effect_start_authority", authority)
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_MISMATCH",
        ):
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=mismatched_request
            )

        # Freezing is a defensive boundary rather than an authorization model:
        # if an in-process attacker mutates a live object by force, adapters
        # fail closed instead of accepting the corrupted correlation.
        object.__setattr__(authority, "effect_key", _hash("tampered-effect-start"))
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_TAMPERED",
        ):
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=request
            )

    def test_effect_start_anchor_proof_is_private_exact_and_fail_closed(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        adapter = mapped[plan.phases[0].name]
        snapshot = driver._snapshot(plan)
        ordinary = driver._request(
            snapshot=snapshot,
            phase=snapshot.phases[0],
            binding=snapshot.binding,
        )
        self.assertIsNone(ordinary._effect_start_anchor_proof)
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "EFFECT_START_ANCHOR_PROOF_REQUIRED",
        ):
            driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=ordinary
            )

        driver.execute_next_physical_full_matrix_v4_phase(
            config=self.config,
            plan=plan,
            adapters=adapters,
            now=NOW,
        )
        request = adapter.requests[0]
        proof = adapter.effect_start_anchor_proofs[0]
        authority = adapter.effect_start_authorities[0]
        start = next(iter(journal.effect_starts.values()))
        self.assertIs(
            proof,
            driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=request
            ),
        )
        self.assertEqual(
            driver.PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA,
            proof.schema,
        )
        self.assertEqual(request.run_id, proof.run_id)
        self.assertEqual(request.plan_sha256, proof.plan_sha256)
        self.assertEqual(request.phase, proof.phase)
        self.assertEqual(request.effect_key, proof.effect_key)
        self.assertEqual(request.phase_request_sha256, proof.phase_request_sha256)
        self.assertEqual(request.binding, proof.binding)
        self.assertEqual(start.claim_id, proof.claim_id)
        self.assertEqual(
            authority.journaled_effect_start_identity_sha256,
            proof.journaled_effect_start_identity_sha256,
        )
        self.assertEqual(0, proof.anchor_genesis_sequence)
        self.assertEqual(0, proof.anchor_previous_sequence)
        self.assertEqual(1, proof.anchor_sequence)
        self.assertEqual("0" * 64, proof.anchor_genesis_head_sha256)
        self.assertEqual("0" * 64, proof.anchor_previous_head_sha256)
        self.assertFalse(proof.writer_authorized)
        self.assertFalse(proof.promotion_authorized)
        self.assertFalse(proof.execution_authorized)
        self.assertFalse(proof.full_matrix_authorized)
        self.assertFalse(hasattr(proof, "journal_path"))
        self.assertFalse(hasattr(proof, "state_root"))

        for operation in (
            lambda: copy.copy(proof),
            lambda: copy.deepcopy(proof),
            lambda: pickle.dumps(proof),
        ):
            with self.assertRaisesRegex(TypeError, "EFFECT_START_ANCHOR_PROOF"):
                operation()

        with self.assertRaisesRegex(
            TypeError,
            "EFFECT_START_ANCHOR_PROOF_CONSTRUCTION_FORBIDDEN",
        ):
            _forge_anchor_proof(proof, capability=object())

        forged = _forge_anchor_proof(
            proof,
            capability=driver._EFFECT_START_ANCHOR_PROOF_CAPABILITY,
        )
        forged_request = driver._request_copy(request)
        object.__setattr__(forged_request, "_effect_start_authority", authority)
        object.__setattr__(forged_request, "_effect_start_anchor_proof", forged)
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "EFFECT_START_ANCHOR_PROOF_REQUIRED",
        ):
            driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=forged_request
            )

        object.__setattr__(proof, "anchor_head_sha256", _hash("tampered-head"))
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "EFFECT_START_ANCHOR_PROOF_TAMPERED",
        ):
            driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=request
            )

    def test_anchor_proof_projection_failure_stops_before_adapter_callback(self) -> None:
        class _ProjectionFaultJournal(_Journal):
            def project_effect_start_anchor_proof(self, *, effect_start, request):
                del effect_start, request
                raise RuntimeError("projection unavailable")

        plan = self.plan()
        root_journal = _ProjectionFaultJournal()
        adapters, _unused, mapped = self.adapters(journal=root_journal)
        phase_adapter = mapped[plan.phases[0].name]
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "EFFECT_START_ANCHOR_PROOF_FAILED",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config,
                plan=plan,
                adapters=adapters,
                now=NOW,
            )
        self.assertEqual([], phase_adapter.requests)
        self.assertEqual(1, len(root_journal.effect_starts))
        self.assertEqual([], root_journal.receipts)

    def test_callback_term_or_readiness_flip_fences_append_after_fresh_clock(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        mapped[plan.phases[0].name].after_execute = lambda: object.__setattr__(
            self.config,
            "maximum_oracle_age_seconds",
            self.config.maximum_oracle_age_seconds - 1,
        )
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "CONFIG_CHANGED_DURING_PHASE",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)
        self.assertEqual(1, len(journal.effect_starts))

        # A term/readiness flip likewise fails the post-callback owner
        # revalidation rather than appending an old evidence receipt.
        second_binding = _binding(suffix="second-normal")
        second_config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=second_binding,
            readiness=_opaque_readiness(second_binding),
            run_id=UUID("8ea994a3-a50a-4f10-bdaf-c75278a0ea74"),
            enabled=True,
        )
        plan = driver.build_physical_full_matrix_v4_execution_plan(config=second_config)
        adapters, journal, mapped = self.adapters()
        changed = replace(
            second_binding,
            writer_epoch=second_binding.writer_epoch + 1,
            writer_lease_id="writer-lease-v4-changed-000001",
        )
        mapped[plan.phases[0].name].after_execute = lambda: object.__setattr__(
            second_config, "binding", changed
        )
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "CONFIG_CHANGED_DURING_PHASE",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=second_config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)
        self.assertEqual(1, len(journal.effect_starts))

        third_binding = _binding(suffix="third-normal")
        third_config = driver.PhysicalFullMatrixV4ExecutionConfig(
            binding=third_binding,
            readiness=_opaque_readiness(third_binding),
            run_id=UUID("9ea994a3-a50a-4f10-bdaf-c75278a0ea74"),
            enabled=True,
        )
        plan = driver.build_physical_full_matrix_v4_execution_plan(config=third_config)
        adapters, journal, mapped = self.adapters()
        mapped[plan.phases[0].name].after_execute = lambda: object.__setattr__(
            third_config,
            "readiness",
            _opaque_readiness(_binding(suffix="third-readiness-flipped")),
        )
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "INITIAL_READINESS_REVALIDATION_FAILED",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=third_config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)
        self.assertEqual(1, len(journal.effect_starts))

    def test_post_callback_trusted_clock_expires_oracle_before_append(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        clock = adapters.trusted_clock
        assert isinstance(clock, _Clock)
        mapped[plan.phases[0].name].after_execute = lambda: setattr(
            clock,
            "now",
            NOW + timedelta(seconds=self.config.maximum_oracle_age_seconds + 1),
        )
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "ORACLE_STALE",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)
        self.assertEqual(1, len(journal.effect_starts))

    def test_post_resolver_clock_fences_before_effect_start(self) -> None:
        """A resolver cannot validate at the pre-callback time then block."""

        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        clock = adapters.trusted_clock
        assert isinstance(clock, _Clock)
        adapters = replace(
            adapters,
            readiness_resolver=_ClockAdvancingResolver(
                clock=clock,
                advance_on_call=1,
                advance=timedelta(seconds=self.config.maximum_oracle_age_seconds + 1),
            ),
        )

        def verify_with_expiry(value, *, now=None):
            if now is not None and now > NOW + timedelta(
                seconds=self.config.maximum_oracle_age_seconds
            ):
                raise gen2_readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError(
                    "simulated-expired-readiness"
                )
            return value.report

        with patch.object(
            driver,
            "require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
            side_effect=verify_with_expiry,
        ), self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "READINESS_PROVENANCE_INVALID",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )

        # The first resolver runs immediately before effect-start.  A stale
        # post-callback answer therefore leaves both the durable effect record
        # and the phase adapter untouched.
        self.assertEqual({}, journal.effect_starts)
        self.assertEqual([], journal.receipts)
        self.assertEqual([], mapped[plan.phases[0].name].requests)

    def test_second_post_resolver_clock_fences_before_phase_adapter(self) -> None:
        """The independent resolver just before the adapter gets the same fence."""

        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        clock = adapters.trusted_clock
        assert isinstance(clock, _Clock)
        adapters = replace(
            adapters,
            readiness_resolver=_ClockAdvancingResolver(
                clock=clock,
                advance_on_call=2,
                advance=timedelta(seconds=self.config.maximum_oracle_age_seconds + 1),
            ),
        )

        def verify_with_expiry(value, *, now=None):
            if now is not None and now > NOW + timedelta(
                seconds=self.config.maximum_oracle_age_seconds
            ):
                raise gen2_readiness_owner.PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError(
                    "simulated-expired-readiness"
                )
            return value.report

        with patch.object(
            driver,
            "require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
            side_effect=verify_with_expiry,
        ), self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "READINESS_PROVENANCE_INVALID",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )

        # The effect-start is intentionally durable before the second resolver,
        # but the stale result must prevent the physical phase adapter itself.
        self.assertEqual(1, len(journal.effect_starts))
        self.assertEqual([], journal.receipts)
        self.assertEqual([], mapped[plan.phases[0].name].requests)

    def test_clock_regression_from_continuity_callback_fails_before_claim(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        clock = adapters.trusted_clock
        assert isinstance(clock, _Clock)
        adapters = replace(
            adapters,
            campaign_continuity_gate=_ClockRegressingCampaignContinuityGate(
                clock=clock,
                regress_on_call=1,
            ),
        )

        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "TRUSTED_CLOCK_REGRESSION",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )

        self.assertEqual({}, journal.effect_starts)
        self.assertEqual([], journal.receipts)
        self.assertTrue(all(not item.requests for item in mapped.values()))

    def test_clock_regression_from_resolver_callback_fails_before_effect_start(self) -> None:
        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        clock = adapters.trusted_clock
        assert isinstance(clock, _Clock)
        adapters = replace(
            adapters,
            readiness_resolver=_ClockRegressingResolver(
                clock=clock,
                regress_on_call=1,
            ),
        )

        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "TRUSTED_CLOCK_REGRESSION",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )

        self.assertEqual({}, journal.effect_starts)
        self.assertEqual([], journal.receipts)
        self.assertTrue(all(not item.requests for item in mapped.values()))

    def test_clock_regression_from_durable_read_never_retries_effect(self) -> None:
        plan = self.plan()
        adapters, _unused_journal, mapped = self.adapters()
        clock = adapters.trusted_clock
        assert isinstance(clock, _Clock)
        journal = _ClockRegressingJournal(clock=clock, regress_on_read=2)
        adapters = replace(adapters, receipt_journal=journal)

        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "TRUSTED_CLOCK_REGRESSION",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )

        # The second read is the durable-after-append observation.  Its
        # callback regression fails closed without issuing another physical
        # operation; the one existing effect remains durably receipted.
        self.assertEqual(2, journal.read_count)
        self.assertEqual(1, len(journal.effect_starts))
        self.assertEqual(1, len(journal.receipts))
        self.assertEqual(1, len(mapped[plan.phases[0].name].requests))
        self.assertTrue(
            all(
                not item.requests
                for phase_name, item in mapped.items()
                if phase_name != plan.phases[0].name
            )
        )

    def test_receipt_bounds_and_trusted_time_order_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "RECEIPT_ENCODING_INVALID",
        ):
            driver.parse_physical_full_matrix_v4_run_receipt(b"{" + b" " * (64 * 1024))

        plan = self.plan()
        adapters, journal, _mapped = self.adapters()
        driver.execute_next_physical_full_matrix_v4_phase(
            config=self.config, plan=plan, adapters=adapters, now=NOW
        )
        first = json.loads(journal.receipts[0])
        first["recorded_at"] = "2026-07-31T13:00:06Z"
        journal.receipts[0] = driver._canonical(
            first, code="test"
        ) + b"\n"
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "RECEIPT_FUTURE",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )

        plan = self.plan()
        adapters, journal, _mapped = self.adapters()
        for _ in range(2):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        second = json.loads(journal.receipts[1])
        second["recorded_at"] = "2026-07-31T12:59:59Z"
        journal.receipts[1] = driver._canonical(
            second, code="test"
        ) + b"\n"
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "RECEIPT_CLOCK_REGRESSION",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )

    def test_stale_or_tampered_oracle_and_receipt_fail_before_next_phase(self) -> None:
        plan = self.plan()
        adapters, journal, _mapped = self.adapters(now=NOW - timedelta(seconds=121))
        with self.assertRaisesRegex(driver.PhysicalFullMatrixV4ExecutionDriverError, "ORACLE_STALE"):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)
        clock = adapters.trusted_clock
        assert isinstance(clock, _Clock)
        self.assertGreaterEqual(clock.calls, 6)

        plan = self.plan()
        adapters, journal, mapped = self.adapters()
        mapped[plan.phases[0].name].tamper_direct_control = True
        with self.assertRaisesRegex(driver.PhysicalFullMatrixV4ExecutionDriverError, "ORACLE_BINDING_MISMATCH"):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        self.assertEqual([], journal.receipts)

        adapters, journal, _mapped = self.adapters()
        driver.execute_next_physical_full_matrix_v4_phase(
            config=self.config, plan=plan, adapters=adapters, now=NOW
        )
        journal.receipts[0] += b" "
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "RECEIPT_(ENCODING|NONCANONICAL|CHAIN)",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )

    def test_racing_claim_returns_durable_existing_receipt_without_repeat_adapter_call(self) -> None:
        plan = self.plan()
        journal = _RaceJournal()
        adapters, _journal, mapped = self.adapters(journal=journal)
        driver.execute_next_physical_full_matrix_v4_phase(
            config=self.config, plan=plan, adapters=adapters, now=NOW
        )
        journal.hide_next_read = True
        result = driver.execute_next_physical_full_matrix_v4_phase(
            config=self.config, plan=plan, adapters=adapters, now=NOW
        )
        self.assertEqual("already-completed-from-append-only-receipt", result.status)
        self.assertEqual(1, len(mapped[plan.phases[0].name].requests))
        self.assertEqual(1, len(journal.receipts))

    def test_transition_existing_receipt_race_resolves_successor_not_retired_old_term(self) -> None:
        plan = self.plan()
        journal = _TransitionRaceJournal()
        adapters, _journal, mapped = self.adapters(journal=journal)
        for _ in range(4):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config, plan=plan, adapters=adapters, now=NOW
            )
        resolver = adapters.readiness_resolver
        assert isinstance(resolver, _Resolver)
        resolver.calls.clear()
        resolver.rejected_binding_sha256.add(self.binding.readiness_binding_sha256)
        journal.stale_once = True
        result = driver.execute_next_physical_full_matrix_v4_phase(
            config=self.config, plan=plan, adapters=adapters, now=NOW
        )
        self.assertEqual("already-completed-from-append-only-receipt", result.status)
        self.assertEqual("witness-promote-ir-v2", result.phase)
        self.assertEqual(1, len(mapped["witness-promote-ir-v2"].requests))
        self.assertTrue(resolver.calls)
        self.assertTrue(
            all(
                item.readiness_binding_sha256 != self.binding.readiness_binding_sha256
                for item in resolver.calls
            )
        )

    def test_pre_effect_callback_faults_fence_adapters_and_only_safe_claims_retry(self) -> None:
        """Failures before a durable effect start cannot reach an adapter.

        A continuity, claim, first-readiness, or effect-start callback may
        fail independently.  Until ``mark_effect_started`` succeeds, retrying
        the same phase is safe; this regression proves that the driver never
        calls a phase adapter early merely to make that retry convenient.
        """

        cases = (
            (
                "continuity",
                "CAMPAIGN_CONTINUITY_UNVERIFIED",
                lambda adapters, root_journal: replace(
                    adapters,
                    campaign_continuity_gate=_FaultInjectingCampaignContinuityGate(
                        fail_calls={1}
                    ),
                ),
                lambda adapters, root_journal: getattr(
                    adapters.campaign_continuity_gate,
                    "fail_calls",
                ).clear(),
            ),
            (
                "claim",
                "PHASE_CLAIM_FAILED",
                lambda adapters, root_journal: adapters,
                lambda adapters, root_journal: root_journal.fail_claim_calls.clear(),
            ),
            (
                "first-readiness",
                "ACTIVE_READINESS_RESOLUTION_FAILED",
                lambda adapters, root_journal: replace(
                    adapters,
                    readiness_resolver=_FaultInjectingResolver(fail_calls={1}),
                ),
                lambda adapters, root_journal: getattr(
                    adapters.readiness_resolver,
                    "fail_calls",
                ).clear(),
            ),
            (
                "effect-start",
                "EFFECT_START_FAILED",
                lambda adapters, root_journal: adapters,
                lambda adapters, root_journal: root_journal.fail_effect_start_calls.clear(),
            ),
        )
        for name, code, configure, clear_fault in cases:
            with self.subTest(callback=name):
                plan = self.plan()
                root_journal = _FaultInjectingJournal(
                    fail_claim_calls={1} if name == "claim" else set(),
                    fail_effect_start_calls={1} if name == "effect-start" else set(),
                )
                adapters, _unused, mapped = self.adapters(journal=root_journal)
                adapters = configure(adapters, root_journal)
                phase_adapter = mapped[plan.phases[0].name]

                with self.assertRaisesRegex(
                    driver.PhysicalFullMatrixV4ExecutionDriverError,
                    code,
                ):
                    driver.execute_next_physical_full_matrix_v4_phase(
                        config=self.config,
                        plan=plan,
                        adapters=adapters,
                        now=NOW,
                    )

                self.assertEqual([], phase_adapter.requests)
                self.assertEqual([], phase_adapter.effect_start_authorities)
                self.assertEqual({}, root_journal.effect_starts)
                self.assertEqual([], root_journal.receipts)

                clear_fault(adapters, root_journal)
                result = driver.execute_next_physical_full_matrix_v4_phase(
                    config=self.config,
                    plan=plan,
                    adapters=adapters,
                    now=NOW,
                )
                self.assertEqual("completed-redacted-phase-receipt", result.status)
                self.assertEqual(1, len(phase_adapter.requests))
                self.assertEqual(1, len(root_journal.effect_starts))
                self.assertEqual(1, len(root_journal.receipts))

    def test_post_effect_start_callback_faults_never_repeat_the_phase_adapter(self) -> None:
        """An indeterminate start is a stop condition, not a retry permit."""

        cases = (
            (
                "second-readiness-before-adapter",
                "ACTIVE_READINESS_RESOLUTION_FAILED",
                lambda adapters, root_journal, mapped, plan: replace(
                    adapters,
                    readiness_resolver=_FaultInjectingResolver(fail_calls={2}),
                ),
                0,
            ),
            (
                "post-adapter-readiness",
                "ACTIVE_READINESS_RESOLUTION_FAILED",
                lambda adapters, root_journal, mapped, plan: replace(
                    adapters,
                    readiness_resolver=_FaultInjectingResolver(fail_calls={3}),
                ),
                1,
            ),
            (
                "pre-append-readiness",
                "ACTIVE_READINESS_RESOLUTION_FAILED",
                lambda adapters, root_journal, mapped, plan: replace(
                    adapters,
                    readiness_resolver=_FaultInjectingResolver(fail_calls={4}),
                ),
                1,
            ),
            (
                "phase-adapter",
                "PHASE_ADAPTER_FAILED",
                lambda adapters, root_journal, mapped, plan: (
                    setattr(mapped[plan.phases[0].name], "raise_after_effect", True)
                    or adapters
                ),
                1,
            ),
            (
                "append-before-durable-record",
                "RECEIPT_APPEND_FAILED",
                lambda adapters, root_journal, mapped, plan: adapters,
                1,
            ),
        )
        for name, code, configure, expected_adapter_calls in cases:
            with self.subTest(callback=name):
                plan = self.plan()
                root_journal = _FaultInjectingJournal(
                    fail_append_before_calls=(
                        {1} if name == "append-before-durable-record" else set()
                    )
                )
                adapters, _unused, mapped = self.adapters(journal=root_journal)
                adapters = configure(adapters, root_journal, mapped, plan)
                phase_adapter = mapped[plan.phases[0].name]

                with self.assertRaisesRegex(
                    driver.PhysicalFullMatrixV4ExecutionDriverError,
                    code,
                ):
                    driver.execute_next_physical_full_matrix_v4_phase(
                        config=self.config,
                        plan=plan,
                        adapters=adapters,
                        now=NOW,
                    )

                self.assertEqual(expected_adapter_calls, len(phase_adapter.requests))
                self.assertEqual(1, len(root_journal.effect_starts))
                self.assertEqual([], root_journal.receipts)

                # Clear a temporary callback fault, then prove the fresh
                # invocation stops at the journal's indeterminate claim.
                resolver = adapters.readiness_resolver
                if isinstance(resolver, _FaultInjectingResolver):
                    resolver.fail_calls.clear()
                phase_adapter.raise_after_effect = False
                root_journal.fail_append_before_calls.clear()
                with self.assertRaisesRegex(
                    driver.PhysicalFullMatrixV4ExecutionDriverError,
                    "EFFECT_INDETERMINATE",
                ):
                    driver.execute_next_physical_full_matrix_v4_phase(
                        config=self.config,
                        plan=plan,
                        adapters=adapters,
                        now=NOW,
                    )
                self.assertEqual(expected_adapter_calls, len(phase_adapter.requests))
                self.assertEqual([], root_journal.receipts)

    def test_post_commit_callback_faults_replay_only_the_receipted_result(self) -> None:
        """A lost append response/read/continuity response cannot repeat phase one."""

        cases = (
            (
                "append-response-after-durable-record",
                "RECEIPT_APPEND_FAILED",
                lambda adapters, root_journal: root_journal.fail_append_after_calls.add(1),
                lambda adapters, root_journal: root_journal.empty_read_calls.add(2),
            ),
            (
                "durable-reread",
                "RECEIPT_JOURNAL_READ_FAILED",
                lambda adapters, root_journal: root_journal.fail_read_calls.add(2),
                lambda adapters, root_journal: root_journal.empty_read_calls.add(3),
            ),
            (
                "final-continuity",
                "CAMPAIGN_CONTINUITY_UNVERIFIED",
                lambda adapters, root_journal: None,
                lambda adapters, root_journal: root_journal.empty_read_calls.add(3),
            ),
        )
        for name, code, configure_journal, prepare_retry in cases:
            with self.subTest(callback=name):
                plan = self.plan()
                root_journal = _FaultInjectingJournal()
                adapters, _unused, mapped = self.adapters(journal=root_journal)
                if name == "final-continuity":
                    adapters = replace(
                        adapters,
                        campaign_continuity_gate=_FaultInjectingCampaignContinuityGate(
                            fail_calls={2}
                        ),
                    )
                configure_journal(adapters, root_journal)
                phase_adapter = mapped[plan.phases[0].name]

                with self.assertRaisesRegex(
                    driver.PhysicalFullMatrixV4ExecutionDriverError,
                    code,
                ):
                    driver.execute_next_physical_full_matrix_v4_phase(
                        config=self.config,
                        plan=plan,
                        adapters=adapters,
                        now=NOW,
                    )

                self.assertEqual(1, len(phase_adapter.requests))
                self.assertEqual(1, len(root_journal.receipts))

                root_journal.fail_append_after_calls.clear()
                root_journal.fail_read_calls.clear()
                gate = adapters.campaign_continuity_gate
                if isinstance(gate, _FaultInjectingCampaignContinuityGate):
                    gate.fail_calls.clear()
                prepare_retry(adapters, root_journal)
                result = driver.execute_next_physical_full_matrix_v4_phase(
                    config=self.config,
                    plan=plan,
                    adapters=adapters,
                    now=NOW,
                )
                self.assertEqual("already-completed-from-append-only-receipt", result.status)
                self.assertEqual(1, len(phase_adapter.requests))
                self.assertEqual(1, len(root_journal.receipts))

    def test_effect_start_authority_is_absent_until_the_private_adapter_request(self) -> None:
        """A caller, resolver, or journal claim cannot mint/observe the handle."""

        plan = self.plan()
        snapshot = driver._snapshot(plan)
        ordinary_request = driver._request(
            snapshot=snapshot,
            phase=snapshot.phases[0],
            binding=snapshot.binding,
        )
        self.assertIsNone(ordinary_request._effect_start_authority)
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "EFFECT_START_AUTHORITY_REQUIRED",
        ):
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=ordinary_request
            )

        root_journal = _FaultInjectingJournal(fail_effect_start_calls={1})
        adapters, _unused, mapped = self.adapters(journal=root_journal)
        phase_adapter = mapped[plan.phases[0].name]
        with self.assertRaisesRegex(
            driver.PhysicalFullMatrixV4ExecutionDriverError,
            "EFFECT_START_FAILED",
        ):
            driver.execute_next_physical_full_matrix_v4_phase(
                config=self.config,
                plan=plan,
                adapters=adapters,
                now=NOW,
            )
        self.assertEqual([], phase_adapter.requests)
        self.assertEqual([], phase_adapter.effect_start_authorities)

        root_journal.fail_effect_start_calls.clear()
        driver.execute_next_physical_full_matrix_v4_phase(
            config=self.config,
            plan=plan,
            adapters=adapters,
            now=NOW,
        )
        self.assertEqual(1, len(phase_adapter.requests))
        self.assertEqual(1, len(phase_adapter.effect_start_authorities))
        self.assertIs(
            phase_adapter.effect_start_authorities[0],
            driver.require_physical_full_matrix_v4_effect_start_authority(
                request=phase_adapter.requests[0]
            ),
        )

    def test_static_import_boundary_excludes_live_effects_and_old_drivers(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            "boto3",
            "os",
            "paramiko",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        self.assertFalse(
            any(name.split(".")[0] in forbidden for name in imported), imported
        )
        self.assertFalse(
            any(
                "physical_full_matrix_execution_driver_v3" in name
                or name.endswith("physical_full_matrix_execution_driver")
                for name in imported
            ),
            imported,
        )
        self.assertIn(
            "core.physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
            imported,
        )
        self.assertNotIn(
            "core.physical_full_matrix_v2_witnessed_campaign_readiness",
            imported,
        )
