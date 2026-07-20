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
    ACTION_LEASE_REFRESH,
    CONTROL_ACTIVE,
    WriterControlError,
    load_writer_snapshot,
    transition_writer_state,
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
