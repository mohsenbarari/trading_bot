"""Authenticated independent Witness ledger for one-time failover sagas."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import httpx

from core.dr_event_protocol import canonical_json_bytes
from core.dr_failover_orchestrator import DrOrchestrationError, FailoverPlan
from core.writer_witness_auth import WITNESS_OPERATION_PATH, sign_witness_request
from core.writer_witness_client import WriterWitnessClientConfig


class WitnessOperationLedger:
    def __init__(self, config: WriterWitnessClientConfig, *, witness_public_key: str) -> None:
        self.config = config
        try:
            public_key = base64.b64decode(witness_public_key, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise DrOrchestrationError("Witness operation-ledger public key is invalid") from exc
        if len(public_key) != 32:
            raise DrOrchestrationError("Witness operation-ledger public key must be Ed25519")
        self.public_key = public_key

    async def _send(
        self,
        plan: FailoverPlan,
        *,
        action: str,
        outcome: str | None = None,
        evidence_hash: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract_version": 1,
            "action": action,
            "operation_id": plan.operation_id,
            "operation_nonce": plan.operation_nonce,
            "plan_hash": plan.plan_hash,
            "expires_at": plan.expires_at.isoformat(),
        }
        if action == "finalize":
            payload.update(outcome=outcome, evidence_hash=evidence_hash)
        body = canonical_json_bytes(payload)
        request_id = plan.operation_nonce if action == "reserve" else plan.operation_id
        headers = sign_witness_request(
            credential=self.config.credential,
            method="POST",
            path=WITNESS_OPERATION_PATH,
            body=body,
            request_id=request_id,
            timestamp=int(datetime.now(timezone.utc).timestamp()),
        )
        try:
            async with httpx.AsyncClient(
                base_url=self.config.base_url.rstrip("/"),
                timeout=self.config.timeout_seconds,
                verify=self.config.verify,
            ) as client:
                response = await client.post(
                    WITNESS_OPERATION_PATH,
                    content=body,
                    headers=headers,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DrOrchestrationError("independent Witness operation ledger is unreachable") from exc
        try:
            result = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise DrOrchestrationError("independent Witness ledger returned invalid JSON") from exc
        if response.status_code != 200 or not isinstance(result, dict):
            raise DrOrchestrationError("independent Witness ledger rejected the operation")
        expected = {
            "contract_version", "status", "operation_id", "operation_nonce",
            "plan_hash", "ledger_receipt_hash", "ledger_receipt_id", "witness_signature",
        }
        if set(result) != expected or result["contract_version"] != 1:
            raise DrOrchestrationError("independent Witness ledger response fields are invalid")
        unsigned = {key: value for key, value in result.items() if key != "witness_signature"}
        try:
            signature = base64.b64decode(str(result["witness_signature"]), validate=True)
            Ed25519PublicKey.from_public_bytes(self.public_key).verify(
                signature,
                canonical_json_bytes(unsigned),
            )
        except (ValueError, binascii.Error, InvalidSignature) as exc:
            raise DrOrchestrationError("independent Witness ledger signature is invalid") from exc
        allowed_statuses = (
            {"reserved", "existing", "expired"}
            if action == "reserve"
            else {str(outcome)}
        )
        if (
            result["operation_id"] != plan.operation_id
            or result["operation_nonce"] != plan.operation_nonce
            or result["plan_hash"] != plan.plan_hash
            or result["status"] not in allowed_statuses
            or not re.fullmatch(r"[0-9a-f]{64}", str(result["ledger_receipt_hash"]))
            or not isinstance(result["ledger_receipt_id"], str)
            or not result["ledger_receipt_id"]
            or len(result["ledger_receipt_id"]) > 64
        ):
            raise DrOrchestrationError(
                "independent Witness ledger receipt does not match the requested operation"
            )
        return {
            "status": result["status"],
            "operation_id": result["operation_id"],
            "operation_nonce": result["operation_nonce"],
            "plan_hash": result["plan_hash"],
            "ledger_receipt_hash": result["ledger_receipt_hash"],
            "ledger_receipt_id": result["ledger_receipt_id"],
        }

    async def reserve(self, plan: FailoverPlan) -> dict[str, Any]:
        return await self._send(plan, action="reserve")

    async def finalize(
        self,
        plan: FailoverPlan,
        *,
        outcome: str,
        evidence_hash: str,
    ) -> dict[str, Any]:
        return await self._send(
            plan,
            action="finalize",
            outcome=outcome,
            evidence_hash=evidence_hash,
        )
