from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.dr_event_protocol import canonical_json_bytes
from core.dr_failover_orchestrator import DrOrchestrationError
from core.dr_operation_ledger import WitnessOperationLedger
from core.writer_witness_auth import WitnessClientCredential
from core.writer_witness_client import WriterWitnessClientConfig


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Client:
    response = None

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        return self.response


class DrOperationLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_witness_signature_binds_global_reservation_receipt(self):
        private = Ed25519PrivateKey.generate()
        public = base64.b64encode(
            private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode()
        plan = SimpleNamespace(
            operation_id=str(uuid4()),
            operation_nonce=str(uuid4()),
            plan_hash="a" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        unsigned = {
            "contract_version": 1,
            "status": "reserved",
            "operation_id": plan.operation_id,
            "operation_nonce": plan.operation_nonce,
            "plan_hash": plan.plan_hash,
            "ledger_receipt_hash": "b" * 64,
            "ledger_receipt_id": "independent-witness-receipt",
        }
        _Client.response = _Response(
            {
                **unsigned,
                "witness_signature": base64.b64encode(
                    private.sign(canonical_json_bytes(unsigned))
                ).decode(),
            }
        )
        ledger = WitnessOperationLedger(
            WriterWitnessClientConfig(
                base_url="https://witness.internal.test",
                credential=WitnessClientCredential(
                    key_id="fi-key",
                    site="webapp_fi",
                    secret="s" * 32,
                ),
            ),
            witness_public_key=public,
        )
        with patch("core.dr_operation_ledger.httpx.AsyncClient", _Client):
            receipt = await ledger.reserve(plan)
            self.assertEqual(receipt["status"], "reserved")
            _Client.response.payload["ledger_receipt_hash"] = "c" * 64
            with self.assertRaisesRegex(DrOrchestrationError, "signature"):
                await ledger.reserve(plan)

            swapped = {**unsigned, "operation_id": str(uuid4())}
            _Client.response = _Response(
                {
                    **swapped,
                    "witness_signature": base64.b64encode(
                        private.sign(canonical_json_bytes(swapped))
                    ).decode(),
                }
            )
            with self.assertRaisesRegex(DrOrchestrationError, "does not match"):
                await ledger.reserve(plan)


if __name__ == "__main__":
    unittest.main()
