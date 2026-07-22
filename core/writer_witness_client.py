"""Authenticated witness transport and automatic local lease refresh."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from core.config import settings
from core.db import DrControlSessionLocal
from core.runtime_identity import RuntimeIdentity, resolve_runtime_identity
from core.webapp_writer_control import (
    ACTION_ACTIVATE,
    ACTION_LEASE_REFRESH,
    CONTROL_ACTIVE,
    WriterControlError,
    load_writer_snapshot,
    transition_writer_state,
    validate_readiness_evidence,
)
from core.writer_witness_auth import (
    WITNESS_TRANSITION_PATH,
    WitnessClientCredential,
    sign_witness_request,
)
from core.writer_witness_contract import (
    ValidatedWitnessLeaseProof,
    WitnessProofError,
    validate_witness_lease_proof,
)
from models.webapp_writer_state import WebappWriterActivationOperation
logger = logging.getLogger(__name__)


class WriterWitnessClientError(RuntimeError):
    """Raised when the witness transport or response cannot authorize renewal."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "writer_witness_client_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class WriterWitnessClientConfig:
    base_url: str
    credential: WitnessClientCredential
    timeout_seconds: float = 3.0
    verify: bool | str = True


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _encoded_command(
    *,
    action: str,
    expected_epoch: int,
    expected_lease_id: str | None,
    request_id: str,
    reason: str,
    lease_duration_seconds: int,
) -> bytes:
    return json.dumps(
        {
            "contract_version": 1,
            "action": action,
            "expected_epoch": int(expected_epoch),
            "expected_lease_id": expected_lease_id,
            "request_id": request_id,
            "reason": reason,
            "lease_duration_seconds": int(lease_duration_seconds),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class WriterWitnessClient:
    def __init__(self, config: WriterWitnessClientConfig) -> None:
        self.config = config

    async def transition(
        self,
        *,
        action: str,
        expected_epoch: int,
        expected_lease_id: str | None,
        request_id: str,
        reason: str,
        lease_duration_seconds: int,
        now: datetime | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        body = _encoded_command(
            action=action,
            expected_epoch=expected_epoch,
            expected_lease_id=expected_lease_id,
            request_id=request_id,
            reason=reason,
            lease_duration_seconds=lease_duration_seconds,
        )
        timestamp = int((now or _utc_now()).timestamp())
        headers = sign_witness_request(
            credential=self.config.credential,
            method="POST",
            path=WITNESS_TRANSITION_PATH,
            body=body,
            request_id=request_id,
            timestamp=timestamp,
        )

        async def send(active_client: httpx.AsyncClient):
            return await active_client.post(
                WITNESS_TRANSITION_PATH,
                content=body,
                headers=headers,
            )

        try:
            if client is not None:
                response = await send(client)
            else:
                async with httpx.AsyncClient(
                    base_url=self.config.base_url.rstrip("/"),
                    timeout=self.config.timeout_seconds,
                    verify=self.config.verify,
                ) as active_client:
                    response = await send(active_client)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise WriterWitnessClientError(
                "writer witness is unreachable",
                code="writer_witness_unreachable",
                retryable=True,
            ) from exc
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise WriterWitnessClientError(
                "writer witness returned invalid JSON",
                code="writer_witness_invalid_response",
                retryable=response.status_code >= 500,
            ) from exc
        if not isinstance(payload, dict):
            raise WriterWitnessClientError(
                "writer witness returned a non-object response",
                code="writer_witness_invalid_response",
                retryable=response.status_code >= 500,
            )
        if response.status_code >= 500:
            raise WriterWitnessClientError(
                "writer witness is not ready",
                code=str(payload.get("code") or "writer_witness_unavailable"),
                retryable=True,
            )
        if response.status_code != 200 or payload.get("accepted") is not True:
            raise WriterWitnessClientError(
                str(payload.get("detail") or "writer witness rejected the transition"),
                code=str(payload.get("code") or "writer_witness_rejected"),
                retryable=False,
            )
        if payload.get("contract_version") != 1 or payload.get("request_id") != request_id:
            raise WriterWitnessClientError(
                "writer witness response does not match the request contract",
                code="writer_witness_invalid_response",
            )
        return payload

    async def status(
        self,
        *,
        request_id: str,
        now: datetime | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        from core.writer_witness_auth import WITNESS_STATUS_PATH

        body = b""
        headers = sign_witness_request(
            credential=self.config.credential,
            method="GET",
            path=WITNESS_STATUS_PATH,
            body=body,
            request_id=request_id,
            timestamp=int((now or _utc_now()).timestamp()),
        )

        async def send(active_client: httpx.AsyncClient):
            return await active_client.get(WITNESS_STATUS_PATH, headers=headers)

        try:
            if client is not None:
                response = await send(client)
            else:
                async with httpx.AsyncClient(
                    base_url=self.config.base_url.rstrip("/"),
                    timeout=self.config.timeout_seconds,
                    verify=self.config.verify,
                ) as active_client:
                    response = await send(active_client)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise WriterWitnessClientError(
                "writer witness status is unreachable",
                code="writer_witness_unreachable",
                retryable=True,
            ) from exc
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise WriterWitnessClientError(
                "writer witness returned invalid status JSON",
                code="writer_witness_invalid_response",
                retryable=response.status_code >= 500,
            ) from exc
        expected = {"contract_version", "accepted", "request_id", "witness_time", "state"}
        if (
            response.status_code != 200
            or not isinstance(payload, dict)
            or set(payload) != expected
            or payload.get("contract_version") != 1
            or payload.get("accepted") is not True
            or payload.get("request_id") != request_id
            or not isinstance(payload.get("state"), dict)
        ):
            raise WriterWitnessClientError(
                "writer witness status response is invalid",
                code="writer_witness_invalid_response",
                retryable=response.status_code >= 500,
            )
        return payload


def writer_witness_client_from_settings(
    identity: RuntimeIdentity | None = None,
) -> WriterWitnessClient:
    runtime_identity = identity or resolve_runtime_identity(settings)
    configuration_reasons = writer_witness_client_configuration_reasons(runtime_identity)
    if configuration_reasons:
        raise WriterWitnessClientError(
            "writer witness client configuration is unsafe: "
            + ",".join(configuration_reasons),
            code="writer_witness_client_configuration_invalid",
        )
    base_url = str(settings.writer_witness_internal_url or "").strip()
    key_id = str(settings.writer_witness_client_key_id or "").strip()
    secret = str(settings.writer_witness_client_secret or "")
    if not runtime_identity.is_webapp_site or not base_url:
        raise WriterWitnessClientError("writer witness client runtime is not configured")
    if not key_id or len(secret.encode("utf-8")) < 32:
        raise WriterWitnessClientError("writer witness client credential is missing or unsafe")
    verify: bool | str = bool(settings.writer_witness_verify_tls)
    if settings.writer_witness_ca_bundle:
        verify = settings.writer_witness_ca_bundle
    return WriterWitnessClient(
        WriterWitnessClientConfig(
            base_url=base_url,
            credential=WitnessClientCredential(
                key_id=key_id,
                site=runtime_identity.physical_site,
                secret=secret,
            ),
            timeout_seconds=max(0.1, float(settings.writer_witness_http_timeout_seconds)),
            verify=verify,
        )
    )


def writer_witness_client_configuration_reasons(
    identity: RuntimeIdentity | None = None,
) -> tuple[str, ...]:
    runtime_identity = identity or resolve_runtime_identity(settings)
    reasons: list[str] = []
    raw_url = str(settings.writer_witness_internal_url or "").strip()
    parsed = urlsplit(raw_url)
    if (
        not raw_url
        or parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        reasons.append("writer_witness_internal_url_invalid")
    if not runtime_identity.is_webapp_site:
        reasons.append("writer_witness_client_not_webapp_site")
    key_id = str(settings.writer_witness_client_key_id or "").strip()
    if not key_id or len(key_id) > 64:
        reasons.append("writer_witness_client_key_id_invalid")
    if len(str(settings.writer_witness_client_secret or "").encode("utf-8")) < 32:
        reasons.append("writer_witness_client_secret_invalid")
    if not settings.writer_witness_verify_tls:
        reasons.append("writer_witness_tls_verification_disabled")
    ca_bundle = str(settings.writer_witness_ca_bundle or "").strip()
    if ca_bundle:
        ca_path = Path(ca_bundle)
        if not ca_path.is_absolute() or not ca_path.is_file():
            reasons.append("writer_witness_ca_bundle_invalid")
    timeout = float(settings.writer_witness_http_timeout_seconds)
    if timeout <= 0 or timeout > 10:
        reasons.append("writer_witness_http_timeout_invalid")
    auth_age = int(settings.writer_witness_auth_max_age_seconds)
    if auth_age <= int(settings.writer_witness_max_clock_skew_seconds) or auth_age > 60:
        reasons.append("writer_witness_auth_window_invalid")
    return tuple(reasons)


async def renew_local_writer_lease_once(
    *,
    client: WriterWitnessClient,
    request_id: str,
    identity: RuntimeIdentity | None = None,
    now: datetime | None = None,
    session_factory: Callable[[], Any] | None = None,
    http_client: httpx.AsyncClient | None = None,
    public_key_base64: str | None = None,
    lease_duration_seconds: int | None = None,
    safety_margin_seconds: int | None = None,
    max_clock_skew_seconds: int | None = None,
) -> ValidatedWitnessLeaseProof:
    runtime_identity = identity or resolve_runtime_identity(settings)
    active_session_factory = session_factory or DrControlSessionLocal
    active_lease_duration = int(
        lease_duration_seconds
        if lease_duration_seconds is not None
        else settings.writer_witness_lease_duration_seconds
    )
    active_safety_margin = int(
        safety_margin_seconds
        if safety_margin_seconds is not None
        else settings.writer_witness_safety_margin_seconds
    )
    active_max_clock_skew = int(
        max_clock_skew_seconds
        if max_clock_skew_seconds is not None
        else settings.writer_witness_max_clock_skew_seconds
    )
    active_public_key = (
        str(public_key_base64)
        if public_key_base64 is not None
        else str(settings.writer_witness_public_key or "")
    )
    async with active_session_factory() as session:
        snapshot = await load_writer_snapshot(session)
    if (
        snapshot.control_state != CONTROL_ACTIVE
        or snapshot.active_site != runtime_identity.physical_site
        or not snapshot.witness_lease_id
        or snapshot.witness_lease_expires_at is None
    ):
        raise WriterWitnessClientError(
            "local WebApp site has no renewable witness lease",
            code="writer_witness_local_term_missing",
        )
    current = now or _utc_now()
    payload = await client.transition(
        action="renew",
        expected_epoch=snapshot.writer_epoch,
        expected_lease_id=snapshot.witness_lease_id,
        request_id=request_id,
        reason="automatic active-writer lease renewal",
        lease_duration_seconds=active_lease_duration,
        now=current,
        client=http_client,
    )
    proof_payload = payload.get("proof")
    try:
        proof = validate_witness_lease_proof(
            proof_payload,
            public_key_base64=active_public_key,
            expected_site=runtime_identity.physical_site,
            expected_epoch=snapshot.writer_epoch,
            now=current,
            safety_margin_seconds=active_safety_margin,
            max_clock_skew_seconds=active_max_clock_skew,
            max_lifetime_seconds=active_lease_duration,
        )
    except WitnessProofError as exc:
        raise WriterWitnessClientError(
            "writer witness returned an unusable signed lease proof",
            code="writer_witness_invalid_proof",
        ) from exc
    async with active_session_factory() as session:
        try:
            await transition_writer_state(
                session,
                action=ACTION_LEASE_REFRESH,
                identity=runtime_identity,
                expected_epoch=snapshot.writer_epoch,
                expected_active_site=runtime_identity.physical_site,
                operator=f"witness-renewer:{runtime_identity.physical_site}",
                reason="validated automatic witness lease refresh",
                witness_proof=proof,
                now=current,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return proof


async def initialize_local_writer_lease_once(
    *,
    client: WriterWitnessClient,
    request_id: str,
    campaign_id: str,
    identity: RuntimeIdentity | None = None,
    now: datetime | None = None,
    session_factory: Callable[[], Any] | None = None,
    http_client: httpx.AsyncClient | None = None,
    public_key_base64: str | None = None,
    lease_duration_seconds: int | None = None,
    safety_margin_seconds: int | None = None,
    max_clock_skew_seconds: int | None = None,
) -> ValidatedWitnessLeaseProof:
    """Acquire/import the one reviewed FI epoch-1 staging bootstrap lease.

    The request id must be persisted by the operator before execution.  An
    ambiguous transport retry therefore replays the Witness receipt instead of
    acquiring a second term.  This function cannot initialize IR or any epoch
    other than the compatibility-migration FI epoch 1.
    """

    runtime_identity = identity or resolve_runtime_identity(settings)
    if runtime_identity.physical_site != "webapp_fi":
        raise WriterWitnessClientError(
            "initial Writer lease may be imported only on WebApp-FI",
            code="writer_witness_bootstrap_site_invalid",
        )
    active_session_factory = session_factory or DrControlSessionLocal
    current = now or _utc_now()
    duration = int(
        lease_duration_seconds
        if lease_duration_seconds is not None
        else settings.writer_witness_lease_duration_seconds
    )
    async with active_session_factory() as session:
        snapshot = await load_writer_snapshot(session)
    if (
        snapshot.control_state != CONTROL_ACTIVE
        or snapshot.active_site != "webapp_fi"
        or snapshot.writer_epoch != 1
    ):
        raise WriterWitnessClientError(
            "initial local Writer state is not WebApp-FI epoch 1",
            code="writer_witness_bootstrap_local_state_invalid",
        )
    reason = f"initial three-site staging migration campaign {campaign_id}"
    payload = await client.transition(
        action="acquire",
        expected_epoch=0,
        expected_lease_id=None,
        request_id=request_id,
        reason=reason,
        lease_duration_seconds=duration,
        now=current,
        client=http_client,
    )
    try:
        proof = validate_witness_lease_proof(
            payload.get("proof"),
            public_key_base64=(
                str(public_key_base64)
                if public_key_base64 is not None
                else str(settings.writer_witness_public_key or "")
            ),
            expected_site="webapp_fi",
            expected_epoch=1,
            now=current,
            safety_margin_seconds=(
                safety_margin_seconds
                if safety_margin_seconds is not None
                else settings.writer_witness_safety_margin_seconds
            ),
            max_clock_skew_seconds=(
                max_clock_skew_seconds
                if max_clock_skew_seconds is not None
                else settings.writer_witness_max_clock_skew_seconds
            ),
            max_lifetime_seconds=duration,
        )
    except WitnessProofError as exc:
        raise WriterWitnessClientError(
            "Writer Witness returned an unusable initial lease proof",
            code="writer_witness_bootstrap_invalid_proof",
        ) from exc
    if snapshot.witness_lease_id is not None:
        if (
            snapshot.witness_lease_id == proof.lease_id
            and snapshot.witness_proof_hash == proof.proof_hash
            and snapshot.witness_transition_id == proof.witness_transition_id
        ):
            return proof
        raise WriterWitnessClientError(
            "local Writer already contains a different Witness lease",
            code="writer_witness_bootstrap_local_lease_conflict",
        )
    async with active_session_factory() as session:
        try:
            await transition_writer_state(
                session,
                action=ACTION_LEASE_REFRESH,
                identity=runtime_identity,
                expected_epoch=1,
                expected_active_site="webapp_fi",
                operator=f"staging-migration:{campaign_id}",
                reason=reason,
                witness_proof=proof,
                now=current,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return proof


async def acquire_and_activate_local_writer_once(
    *,
    client: WriterWitnessClient,
    status_request_id: str,
    acquire_request_id: str,
    operation_id: str,
    target_epoch: int,
    readiness_payload: dict[str, Any],
    identity: RuntimeIdentity | None = None,
    now: datetime | None = None,
    session_factory: Callable[[], Any] | None = None,
    http_client: httpx.AsyncClient | None = None,
    public_key_base64: str | None = None,
    lease_duration_seconds: int | None = None,
    safety_margin_seconds: int | None = None,
    max_clock_skew_seconds: int | None = None,
) -> ValidatedWitnessLeaseProof:
    """Acquire the exact next global term and activate one fenced target once."""

    runtime_identity = identity or resolve_runtime_identity(settings)
    if not runtime_identity.is_webapp_site or target_epoch < 2:
        raise WriterWitnessClientError(
            "target Writer activation identity/epoch is invalid",
            code="writer_witness_target_activation_invalid",
        )
    current = now or _utc_now()
    duration = int(
        lease_duration_seconds
        if lease_duration_seconds is not None
        else settings.writer_witness_lease_duration_seconds
    )
    readiness = validate_readiness_evidence(
        readiness_payload,
        target_site=runtime_identity.physical_site,
        writer_epoch=target_epoch,
        now=current,
    )
    active_session_factory = session_factory or DrControlSessionLocal

    async with active_session_factory() as session:
        operation = await session.get(
            WebappWriterActivationOperation,
            operation_id,
            with_for_update=True,
        )
        if operation is not None and (
            operation.status_request_id != status_request_id
            or operation.acquire_request_id != acquire_request_id
            or operation.target_site != runtime_identity.physical_site
            or int(operation.target_epoch) != target_epoch
            or int(operation.predecessor_epoch) != target_epoch - 1
        ):
            raise WriterWitnessClientError(
                "target activation operation was reused with different parameters",
                code="writer_witness_target_operation_conflict",
            )

    if operation is None:
        status_payload = await client.status(
            request_id=status_request_id,
            now=current,
            client=http_client,
        )
        witness_state = status_payload["state"]
        try:
            witness_epoch = int(witness_state["writer_epoch"])
            witness_lease_id = witness_state.get("lease_id")
            expires_raw = witness_state.get("expires_at")
            expires_at = (
                datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
                if expires_raw is not None
                else None
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WriterWitnessClientError(
                "writer witness status state is malformed",
                code="writer_witness_invalid_response",
            ) from exc
        if witness_epoch != target_epoch - 1:
            raise WriterWitnessClientError(
                "Witness is not at the exact predecessor epoch",
                code="writer_witness_target_epoch_stale",
            )
        if expires_at is not None:
            if expires_at.tzinfo is None:
                raise WriterWitnessClientError(
                    "Witness lease expiry lacks timezone",
                    code="writer_witness_invalid_response",
                )
            if expires_at.astimezone(timezone.utc) > current:
                raise WriterWitnessClientError(
                    "predecessor Witness lease is still live",
                    code="writer_witness_predecessor_lease_live",
                    retryable=True,
                )
        async with active_session_factory() as session:
            operation = WebappWriterActivationOperation(
                operation_id=operation_id,
                status_request_id=status_request_id,
                acquire_request_id=acquire_request_id,
                target_site=runtime_identity.physical_site,
                target_epoch=target_epoch,
                predecessor_epoch=witness_epoch,
                predecessor_lease_id=witness_lease_id,
                state="planned",
            )
            session.add(operation)
            await session.commit()

    proof_payload: dict[str, Any]
    if operation.proof_json:
        try:
            proof_payload = json.loads(operation.proof_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WriterWitnessClientError(
                "persisted target activation proof is invalid",
                code="writer_witness_target_operation_corrupt",
            ) from exc
    else:
        # The predecessor inputs were committed locally before this remote
        # mutation.  An exact retry therefore reaches Witness's durable
        # request receipt even if the first response was lost after commit.
        payload = await client.transition(
            action="acquire",
            expected_epoch=int(operation.predecessor_epoch),
            expected_lease_id=operation.predecessor_lease_id,
            request_id=acquire_request_id,
            reason=f"approved failover operation {operation_id}",
            lease_duration_seconds=duration,
            now=current,
            client=http_client,
        )
        proof_payload = payload.get("proof")

    try:
        proof = validate_witness_lease_proof(
            proof_payload,
            public_key_base64=(
                str(public_key_base64)
                if public_key_base64 is not None
                else str(settings.writer_witness_public_key or "")
            ),
            expected_site=runtime_identity.physical_site,
            expected_epoch=target_epoch,
            now=current,
            safety_margin_seconds=(
                safety_margin_seconds
                if safety_margin_seconds is not None
                else settings.writer_witness_safety_margin_seconds
            ),
            max_clock_skew_seconds=(
                max_clock_skew_seconds
                if max_clock_skew_seconds is not None
                else settings.writer_witness_max_clock_skew_seconds
            ),
            max_lifetime_seconds=duration,
        )
    except WitnessProofError as exc:
        raise WriterWitnessClientError(
            "Writer Witness returned an unusable target lease proof",
            code="writer_witness_target_proof_invalid",
        ) from exc

    if operation.proof_json is None:
        async with active_session_factory() as session:
            stored = await session.get(
                WebappWriterActivationOperation,
                operation_id,
                with_for_update=True,
            )
            if stored is None or stored.state != "planned":
                raise WriterWitnessClientError(
                    "target activation operation changed during Witness acquisition",
                    code="writer_witness_target_operation_conflict",
                )
            stored.proof_json = json.dumps(
                proof_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            stored.proof_hash = proof.proof_hash
            stored.state = "witness_acquired"
            stored.updated_at = current
            await session.commit()

    async with active_session_factory() as session:
        snapshot = await load_writer_snapshot(session)
    if snapshot.control_state == CONTROL_ACTIVE:
        if (
            snapshot.active_site == runtime_identity.physical_site
            and snapshot.writer_epoch == target_epoch
            and snapshot.witness_lease_id == proof.lease_id
            and snapshot.witness_proof_hash == proof.proof_hash
        ):
            async with active_session_factory() as update_session:
                stored = await update_session.get(
                    WebappWriterActivationOperation,
                    operation_id,
                    with_for_update=True,
                )
                if stored is not None and stored.state != "local_activated":
                    stored.state = "local_activated"
                    stored.updated_at = current
                    await update_session.commit()
            return proof
        raise WriterWitnessClientError(
            "target database already contains another active Writer term",
            code="writer_witness_target_local_conflict",
        )
    if (
        snapshot.control_state != "fenced"
        or snapshot.active_site is not None
        or snapshot.writer_epoch >= target_epoch
    ):
        raise WriterWitnessClientError(
            "target database is not a fenced older term",
            code="writer_witness_target_local_state_invalid",
        )
    async with active_session_factory() as session:
        try:
            await transition_writer_state(
                session,
                action=ACTION_ACTIVATE,
                identity=runtime_identity,
                expected_epoch=snapshot.writer_epoch,
                expected_active_site=None,
                operator=f"failover-orchestrator:{operation_id}",
                reason=f"approved failover operation {operation_id}",
                evidence=readiness,
                witness_proof=proof,
                now=current,
            )
            stored = await session.get(
                WebappWriterActivationOperation,
                operation_id,
                with_for_update=True,
            )
            if stored is None or stored.proof_hash != proof.proof_hash:
                raise WriterWitnessClientError(
                    "target activation receipt is missing during local activation",
                    code="writer_witness_target_operation_conflict",
                )
            stored.state = "local_activated"
            stored.updated_at = current
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return proof


async def drain_local_writer_lease_once(
    *,
    client: WriterWitnessClient,
    request_id: str,
    operation_id: str,
    expected_epoch: int,
    identity: RuntimeIdentity | None = None,
    now: datetime | None = None,
    session_factory: Callable[[], Any] | None = None,
    http_client: httpx.AsyncClient | None = None,
    lease_duration_seconds: int | None = None,
) -> dict[str, Any]:
    """Place the current source lease into non-renewable drain state once."""

    runtime_identity = identity or resolve_runtime_identity(settings)
    if not runtime_identity.is_webapp_site:
        raise WriterWitnessClientError(
            "Writer lease drain requires a WebApp site",
            code="writer_witness_drain_site_invalid",
        )
    active_session_factory = session_factory or DrControlSessionLocal
    async with active_session_factory() as session:
        snapshot = await load_writer_snapshot(session)
    if (
        snapshot.control_state != CONTROL_ACTIVE
        or snapshot.active_site != runtime_identity.physical_site
        or snapshot.writer_epoch != expected_epoch
        or not snapshot.witness_lease_id
    ):
        raise WriterWitnessClientError(
            "local source has no exact active Witness lease to drain",
            code="writer_witness_drain_local_state_invalid",
        )
    payload = await client.transition(
        action="drain",
        expected_epoch=expected_epoch,
        expected_lease_id=snapshot.witness_lease_id,
        request_id=request_id,
        reason=f"approved failover operation {operation_id}",
        lease_duration_seconds=int(
            lease_duration_seconds
            if lease_duration_seconds is not None
            else settings.writer_witness_lease_duration_seconds
        ),
        now=now or _utc_now(),
        client=http_client,
    )
    state = payload.get("state")
    if (
        not isinstance(state, dict)
        or state.get("holder_site") != runtime_identity.physical_site
        or state.get("writer_epoch") != expected_epoch
        or state.get("lease_id") != snapshot.witness_lease_id
        or state.get("lease_status") != "draining"
        or not isinstance(state.get("expires_at"), str)
    ):
        raise WriterWitnessClientError(
            "Writer Witness returned an invalid drain receipt",
            code="writer_witness_drain_receipt_invalid",
        )
    return payload


async def writer_witness_renewal_loop() -> None:
    """Renew with a stable request id across ambiguous transport failures."""

    if not settings.writer_witness_required or not settings.writer_witness_auto_renew_enabled:
        raise WriterControlError(
            "automatic witness renewal requires both enforcement and auto-renew flags"
        )
    identity = resolve_runtime_identity(settings)
    client = writer_witness_client_from_settings(identity)
    interval = max(1, int(settings.writer_witness_renew_interval_seconds))
    retry_interval = max(1, min(5, interval))
    while True:
        request_id = str(uuid4())
        while True:
            try:
                proof = await renew_local_writer_lease_once(
                    client=client,
                    request_id=request_id,
                    identity=identity,
                )
            except asyncio.CancelledError:
                raise
            except WriterWitnessClientError as exc:
                logger.warning(
                    "Automatic writer witness renewal failed",
                    extra={
                        "event": "writer_witness.renewal.failed",
                        "physical_site": identity.physical_site,
                        "request_id": request_id,
                        "error_code": exc.code,
                        "retryable": exc.retryable,
                    },
                )
                if exc.retryable:
                    await asyncio.sleep(retry_interval)
                    continue
                break
            except Exception:
                logger.exception(
                    "Automatic writer witness proof refresh failed closed",
                    extra={
                        "event": "writer_witness.refresh.failed",
                        "physical_site": identity.physical_site,
                        "request_id": request_id,
                    },
                )
                break
            logger.info(
                "Automatic writer witness lease refreshed",
                extra={
                    "event": "writer_witness.renewal.succeeded",
                    "physical_site": identity.physical_site,
                    "writer_epoch": proof.writer_epoch,
                    "lease_id": proof.lease_id,
                    "expires_at": proof.expires_at.isoformat(),
                },
            )
            break
        await asyncio.sleep(interval)
