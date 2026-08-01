from __future__ import annotations

import ast
from datetime import timedelta
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import physical_wal_v2_witness_roundtrip_contract as roundtrip
from core import physical_wal_v2_witness_roundtrip_source_outbox as outbox
from core.append_only_sync_delta_batch import canonical_json_bytes
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW
from tests.test_physical_wal_v2_witness_roundtrip_contract import (
    PhysicalWalV2WitnessRoundtripContractTests,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wal_v2_witness_roundtrip_source_outbox.py"
)


@unittest.skipUnless(os.geteuid() == 0, "outbox storage is deliberately root-only")
class PhysicalWalV2WitnessRoundtripSourceOutboxTests(unittest.TestCase):
    """Exercise the real contract grammar through the FI-only durable boundary."""

    def setUp(self) -> None:
        self.contract_fixture = PhysicalWalV2WitnessRoundtripContractTests("runTest")
        self.contract_fixture.setUp()
        self.addCleanup(self.contract_fixture.tearDown)
        self.tempdir = tempfile.TemporaryDirectory(prefix="v2-witness-source-outbox-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        os.chmod(self.root, 0o700)
        export = self.contract_fixture._recovery_export()
        certificate = self.contract_fixture._certificate(export)
        self.certificate = roundtrip.verify_physical_wal_v2_witness_context_certificate(
            certificate,
            config=self.contract_fixture.config,
            now=NOW,
        )
        self.config = outbox.PhysicalWalV2WitnessRoundtripSourceOutboxConfig(
            state_root=self.root,
            roundtrip_config=self.contract_fixture.config,
            enabled=True,
            maximum_entries=8,
        )
        self.request_id = "v2-witness-source-request-000101"
        self.request_nonce = "Q" * 22
        self.outbox_id = "v2-witness-outbox-000101"
        self.outbox_nonce = "O" * 22
        self.expires_at = NOW + timedelta(seconds=25)

    def _enqueue(self, **changes: object) -> outbox.PhysicalWalV2WitnessRoundtripSourceOutboxResult:
        values: dict[str, object] = {
            "config": self.config,
            "context_certificate": self.certificate,
            "request_id": self.request_id,
            "request_nonce": self.request_nonce,
            "outbox_id": self.outbox_id,
            "outbox_nonce": self.outbox_nonce,
            "expires_at": self.expires_at,
            "source_signer": self.contract_fixture.fixture.source,
            "fi_outbox_signer": self.contract_fixture.fi_outbox,
        }
        values.update(changes)
        with patch.object(outbox, "_host_now", return_value=NOW):
            return outbox.enqueue_physical_wal_v2_witness_source_envelope(**values)  # type: ignore[arg-type]

    def test_real_certificate_produces_exact_durable_retry_bytes(self) -> None:
        first = self._enqueue()
        second = self._enqueue()
        self.assertFalse(first.idempotent)
        self.assertTrue(second.idempotent)
        self.assertEqual(first.canonical_source_envelope, second.canonical_source_envelope)
        self.assertEqual(first.source_envelope_sha256, second.source_envelope_sha256)
        self.assertEqual(self.certificate.certificate_sha256, first.context_certificate_sha256)
        verified = roundtrip.verify_physical_wal_v2_witness_source_envelope(
            first.canonical_source_envelope,
            config=self.contract_fixture.config,
            now=NOW,
        )
        self.assertEqual(self.request_id, verified.request_id)
        self.assertEqual(self.outbox_id, verified.outbox_id)
        state = json.loads((self.root / outbox._DIRECTORY / outbox._STATE_FILENAME).read_text("ascii"))
        self.assertEqual("completed", state["entries"][0]["status"])
        self.assertNotIn("receiver_ledger_receipt", state["entries"][0])
        self.assertNotIn("target_recovery_evidence", state["entries"][0])

    def test_same_request_identity_with_mutated_outbox_is_conflict_not_reseal(self) -> None:
        self._enqueue()
        with self.assertRaisesRegex(
            outbox.PhysicalWalV2WitnessRoundtripSourceOutboxError,
            "INTENT_REUSE_CONFLICT",
        ):
            self._enqueue(outbox_id="v2-witness-outbox-000102")

    def test_post_reservation_signing_failure_stays_indeterminate(self) -> None:
        with patch.object(
            outbox,
            "build_physical_wal_v2_witness_source_envelope",
            side_effect=roundtrip.PhysicalWalV2WitnessRoundtripError("test-signing-failure"),
        ):
            with self.assertRaisesRegex(
                outbox.PhysicalWalV2WitnessRoundtripSourceOutboxError,
                "SOURCE_ENVELOPE_INVALID",
            ):
                self._enqueue()
        with self.assertRaisesRegex(
            outbox.PhysicalWalV2WitnessRoundtripSourceOutboxError,
            "RESERVATION_INDETERMINATE",
        ):
            self._enqueue()
        state = json.loads((self.root / outbox._DIRECTORY / outbox._STATE_FILENAME).read_text("ascii"))
        self.assertEqual("reserved", state["entries"][0]["status"])
        self.assertIsNone(state["entries"][0]["source_envelope_base64"])

    def test_raw_context_is_rejected_before_any_state_or_signing_path(self) -> None:
        with self.assertRaisesRegex(
            outbox.PhysicalWalV2WitnessRoundtripSourceOutboxError,
            "CONTEXT_CERTIFICATE_CAPABILITY_REQUIRED",
        ):
            self._enqueue(context_certificate=self.contract_fixture.fixture.context)
        self.assertFalse((self.root / outbox._DIRECTORY).exists())

    def test_persisted_clock_floor_rejects_rollback_before_retry(self) -> None:
        self._enqueue()
        with patch.object(outbox, "_host_now", return_value=NOW - timedelta(seconds=1)):
            with self.assertRaisesRegex(
                outbox.PhysicalWalV2WitnessRoundtripSourceOutboxError,
                "CLOCK_ROLLBACK_DETECTED",
            ):
                outbox.enqueue_physical_wal_v2_witness_source_envelope(
                    config=self.config,
                    context_certificate=self.certificate,
                    request_id=self.request_id,
                    request_nonce=self.request_nonce,
                    outbox_id=self.outbox_id,
                    outbox_nonce=self.outbox_nonce,
                    expires_at=self.expires_at,
                    source_signer=self.contract_fixture.fixture.source,
                    fi_outbox_signer=self.contract_fixture.fi_outbox,
                )

    def test_tampered_durable_request_digest_is_rejected_before_retry(self) -> None:
        self._enqueue()
        state_path = self.root / outbox._DIRECTORY / outbox._STATE_FILENAME
        state = json.loads(state_path.read_text("ascii"))
        state["entries"][0]["source_request_sha256"] = "f" * 64
        state_path.write_bytes(canonical_json_bytes(state))
        os.chmod(state_path, 0o600)
        with self.assertRaisesRegex(
            outbox.PhysicalWalV2WitnessRoundtripSourceOutboxError,
            "ENTRY_REQUEST_INVALID",
        ):
            self._enqueue()

    def test_source_has_no_preflight_v1_peer_or_provider_transport_surface(self) -> None:
        source = inspect.getsource(outbox)
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        for forbidden in (
            "socket",
            "subprocess",
            "requests",
            "boto3",
            "core.dedicated_host_preflight_runtime_transport",
            "core.physical_wal_v2_remote_ack_receiver_ledger",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertNotIn("physical_wal_v1", source)
        self.assertNotIn("connect(", source)
        self.assertIn("os.O_NOFOLLOW", source)
        self.assertIn("dir_fd=", source)
        self.assertIn("os.fsync", source)
        self.assertIn("fcntl.flock", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
