"""Focused adversarial checks for the V2 Witness durable-roundtrip ledger.

The functional fixture is added beside the final portable-wire contract.  The
checks here deliberately begin with the properties that must never regress:
this boundary is an fd-anchored local ledger and never a hidden direct
FI-to-IR transport implementation.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core import physical_wal_v2_witness_roundtrip_witness_ledger as ledger
from core import physical_wal_v2_witness_roundtrip_contract as roundtrip
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW
from tests.test_physical_wal_v2_witness_roundtrip_contract import (
    PhysicalWalV2WitnessRoundtripContractTests,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wal_v2_witness_roundtrip_witness_ledger.py"
)


class PhysicalWalV2WitnessRoundtripWitnessLedgerStructuralTests(unittest.TestCase):
    def test_record_filename_grammar_is_exact(self) -> None:
        digest = "a" * 64
        self.assertIsNotNone(ledger._RECORD_RE.fullmatch(f"{'1':0>20}-{digest}.json"))
        self.assertIsNone(ledger._RECORD_RE.fullmatch(f"{'1':0>20}-{digest}\\.json"))
        self.assertIsNone(ledger._RECORD_RE.fullmatch(f"{'1':0>20}-{digest}.json.bak"))

    def test_source_has_no_direct_peer_or_provider_transport_surface(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        banned_import_roots = {
            "boto3",
            "http",
            "httpx",
            "paramiko",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        imported: set[str] = set()
        call_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    call_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    call_names.add(node.func.attr)
        self.assertFalse(imported & banned_import_roots)
        self.assertFalse(
            call_names
            & {
                "connect",
                "create_connection",
                "get",
                "post",
                "put",
                "request",
                "run",
                "send",
                "ssh",
            }
        )

    def test_source_uses_fd_anchored_create_only_durability_primitives(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("os.O_NOFOLLOW", source)
        self.assertIn("dir_fd=", source)
        self.assertIn("os.O_EXCL", source)
        self.assertIn("os.fsync", source)
        self.assertIn("fcntl.flock", source)


class PhysicalWalV2WitnessRoundtripWitnessLedgerStorageTests(unittest.TestCase):
    """Exercise the contract-independent root-owned storage invariants."""

    NOW = datetime(2026, 7, 31, 20, 0, 0, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="v2-witness-roundtrip-ledger-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        os.chmod(self.root, 0o700)
        metadata = {
            "schema": ledger._BINDING_SCHEMA,
            "test_binding_sha256": "a" * 64,
        }
        self.config = ledger._Config(
            root=self.root,
            roundtrip_config=object(),
            binding_metadata=metadata,
            binding_sha256=hashlib.sha256(ledger._canonical(metadata, code="TEST")).hexdigest(),
            maximum_records=8,
        )

    @staticmethod
    def _digest(raw: bytes | None) -> str | None:
        return None if raw is None else hashlib.sha256(raw).hexdigest()

    def _context_record(self, *, previous_head: str | None = None) -> ledger._Record:
        export = b'{"signed":"recovery-export"}'
        certificate = b'{"signed":"context-certificate"}'
        draft = ledger._Record(
            sequence=1,
            previous_head_sha256=previous_head or self.config.binding_sha256,
            stage=ledger._CONTEXT_STAGE,
            accepted_at=self.NOW,
            clock_floor=self.NOW,
            witness_ledger_binding_sha256=self.config.binding_sha256,
            ledger_entry_sha256="a" * 64,
            context_sha256="b" * 64,
            recovery_export=export,
            recovery_export_sha256=self._digest(export),
            witness_context_certificate=certificate,
            witness_context_certificate_sha256=self._digest(certificate),
            fi_envelope=None,
            fi_envelope_sha256=None,
            ir_durable_assertion=None,
            ir_durable_assertion_sha256=None,
            witness_roundtrip_attestation=None,
            witness_roundtrip_attestation_sha256=None,
            record_sha256="c" * 64,
        )
        with_entry = replace(draft, ledger_entry_sha256=ledger._ledger_entry_digest(draft))
        return replace(with_entry, record_sha256=ledger._record_digest(with_entry))

    def _open_and_load(self, *, now: datetime | None = None) -> ledger._State:
        with ledger._locked(self.config) as storage:
            ledger._init_storage(storage, config=self.config)
            return ledger._read_records(storage, config=self.config, trusted_now=now or self.NOW)

    def test_restart_rebuilds_create_only_hash_chain(self) -> None:
        record = self._context_record()
        with ledger._locked(self.config) as storage:
            ledger._init_storage(storage, config=self.config)
            ledger._append_record(storage, record=record)
        restarted = self._open_and_load()
        self.assertEqual((record,), restarted.records)
        self.assertEqual(record.record_sha256, restarted.head_sha256)
        self.assertEqual(self.NOW, restarted.clock_floor)

    def test_create_only_collision_never_rewrites_a_record(self) -> None:
        record = self._context_record()
        with ledger._locked(self.config) as storage:
            ledger._init_storage(storage, config=self.config)
            ledger._append_record(storage, record=record)
            with self.assertRaisesRegex(
                ledger.PhysicalWalV2WitnessRoundtripWitnessLedgerError,
                "RECORD_COLLISION",
            ):
                ledger._append_record(storage, record=record)
        restarted = self._open_and_load()
        self.assertEqual((record,), restarted.records)

    def test_persisted_clock_floor_rejects_rollback(self) -> None:
        record = self._context_record()
        with ledger._locked(self.config) as storage:
            ledger._init_storage(storage, config=self.config)
            ledger._append_record(storage, record=record)
        with self.assertRaisesRegex(
            ledger.PhysicalWalV2WitnessRoundtripWitnessLedgerError,
            "CLOCK_ROLLBACK_DETECTED",
        ):
            self._open_and_load(now=datetime(2026, 7, 31, 19, 59, 59, tzinfo=timezone.utc))

    def test_symlinked_records_directory_is_rejected_before_open(self) -> None:
        self._open_and_load()
        records = self.root / ledger._DIRECTORY / ledger._RECORDS_DIRECTORY
        records.rmdir()
        os.symlink("/tmp", records)
        with self.assertRaisesRegex(
            ledger.PhysicalWalV2WitnessRoundtripWitnessLedgerError,
            "DIRECTORY_UNSAFE",
        ):
            self._open_and_load()


class PhysicalWalV2WitnessRoundtripWitnessLedgerFunctionalTests(unittest.TestCase):
    """Exercise both durable stages with only canonical signed test bytes."""

    def setUp(self) -> None:
        self.contract_fixture = PhysicalWalV2WitnessRoundtripContractTests("runTest")
        self.contract_fixture.setUp()
        self.addCleanup(self.contract_fixture.tearDown)
        self.tempdir = tempfile.TemporaryDirectory(prefix="v2-witness-roundtrip-functional-")
        self.addCleanup(self.tempdir.cleanup)
        self.state_root = Path(self.tempdir.name)
        os.chmod(self.state_root, 0o700)
        self.config = ledger.PhysicalWalV2WitnessRoundtripWitnessLedgerConfig(
            state_root=self.state_root,
            roundtrip_config=self.contract_fixture.config,
            enabled=True,
            maximum_records=8,
        )

    def _runtime(self, *, at: datetime = NOW):
        with mock.patch.object(ledger, "_host_now", return_value=at):
            return ledger.open_physical_wal_v2_witness_roundtrip_witness_ledger(self.config)

    def _export_raw(self) -> bytes:
        raw = self.contract_fixture._recovery_export()
        return roundtrip.verify_physical_wal_v2_witness_recovery_export(
            raw,
            config=self.contract_fixture.config,
            now=NOW,
        ).canonical_export

    def _certify(self, runtime, raw_export: bytes, *, at: datetime = NOW, live=None):
        with (
            mock.patch.object(ledger, "_host_now", return_value=at),
            mock.patch.object(
                roundtrip,
                "_check_live_activation",
                return_value=live or self.contract_fixture.live,
            ),
        ):
            return ledger.certify_physical_wal_v2_witness_roundtrip_context(
                runtime=runtime,
                recovery_export=raw_export,
                witnessed_term=object(),
                activation=object(),
                witness_signer=self.contract_fixture.witness,
            )

    def _envelope_and_assertion(self, certificate_raw: bytes) -> tuple[bytes, bytes]:
        certificate = roundtrip.verify_physical_wal_v2_witness_context_certificate(
            certificate_raw,
            config=self.contract_fixture.config,
            now=NOW,
        )
        envelope_mapping = roundtrip.build_physical_wal_v2_witness_source_envelope(
            config=self.contract_fixture.config,
            context_certificate=certificate,
            source_request=self.contract_fixture.fixture.request.canonical_request,
            outbox_id="v2-ledger-outbox-000001",
            outbox_nonce="O" * 22,
            expires_at=NOW + timedelta(seconds=25),
            fi_outbox_signer=self.contract_fixture.fi_outbox,
            now=NOW,
        )
        envelope = roundtrip.verify_physical_wal_v2_witness_source_envelope(
            envelope_mapping,
            config=self.contract_fixture.config,
            now=NOW,
        )
        assertion_mapping, _issued = self.contract_fixture._assertion(envelope_mapping)
        assertion = roundtrip.verify_physical_wal_v2_witness_ir_durable_assertion(
            assertion_mapping,
            config=self.contract_fixture.config,
            now=NOW,
        )
        return envelope.canonical_envelope, assertion.canonical_assertion

    def _attest(
        self,
        runtime,
        certificate: bytes,
        envelope: bytes,
        assertion: bytes,
        *,
        at: datetime = NOW,
        live=None,
    ):
        with (
            mock.patch.object(ledger, "_host_now", return_value=at),
            mock.patch.object(
                roundtrip,
                "_check_live_activation",
                return_value=live or self.contract_fixture.live,
            ),
        ):
            return ledger.attest_physical_wal_v2_witness_roundtrip(
                runtime=runtime,
                context_certificate=certificate,
                fi_source_envelope=envelope,
                ir_durable_assertion=assertion,
                witnessed_term=object(),
                activation=object(),
                witness_signer=self.contract_fixture.witness,
            )

    def test_durable_two_stage_roundtrip_restarts_and_retries_only_exact_bytes(self) -> None:
        runtime = self._runtime()
        export = self._export_raw()
        context = self._certify(runtime, export)
        self.assertFalse(context.idempotent)
        self.assertEqual(1, context.sequence)
        self.assertIsNotNone(context.witness_context_certificate)
        retry_context = self._certify(runtime, export)
        self.assertTrue(retry_context.idempotent)
        self.assertEqual(context.witness_context_certificate, retry_context.witness_context_certificate)

        certificate = context.witness_context_certificate
        assert certificate is not None
        envelope, assertion = self._envelope_and_assertion(certificate)
        final = self._attest(runtime, certificate, envelope, assertion)
        self.assertFalse(final.idempotent)
        self.assertEqual(2, final.sequence)
        self.assertIsNotNone(final.witness_roundtrip_attestation)
        self.assertNotEqual(context.ledger_entry_sha256, final.ledger_entry_sha256)

        restarted = self._runtime()
        retry_final = self._attest(restarted, certificate, envelope, assertion)
        self.assertTrue(retry_final.idempotent)
        self.assertEqual(final.witness_roundtrip_attestation, retry_final.witness_roundtrip_attestation)
        attestation_raw = retry_final.witness_roundtrip_attestation
        assert attestation_raw is not None
        verified = roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            attestation_raw,
            config=self.contract_fixture.config,
            now=NOW,
        )
        self.assertEqual(2, verified.witness_sequence)
        self.assertEqual(final.ledger_entry_sha256, verified.witness_ledger_entry_sha256)
        self.assertEqual(context.ledger_head_sha256, verified.witness_ledger_previous_head_sha256)

    def test_replay_collision_and_stale_runtime_fail_closed(self) -> None:
        export = self._export_raw()
        first = self._runtime()
        stale = self._runtime()
        context = self._certify(first, export)
        with self.assertRaisesRegex(
            ledger.PhysicalWalV2WitnessRoundtripWitnessLedgerError,
            "STALE_RUNTIME",
        ):
            self._certify(stale, export)

        certificate = context.witness_context_certificate
        assert certificate is not None
        envelope, assertion = self._envelope_and_assertion(certificate)
        self._attest(first, certificate, envelope, assertion)

        verified_certificate = roundtrip.verify_physical_wal_v2_witness_context_certificate(
            certificate,
            config=self.contract_fixture.config,
            now=NOW,
        )
        other_envelope_mapping = roundtrip.build_physical_wal_v2_witness_source_envelope(
            config=self.contract_fixture.config,
            context_certificate=verified_certificate,
            source_request=self.contract_fixture.fixture.request.canonical_request,
            outbox_id="v2-ledger-outbox-000002",
            outbox_nonce="N" * 22,
            expires_at=NOW + timedelta(seconds=25),
            fi_outbox_signer=self.contract_fixture.fi_outbox,
            now=NOW,
        )
        other_envelope = roundtrip.verify_physical_wal_v2_witness_source_envelope(
            other_envelope_mapping,
            config=self.contract_fixture.config,
            now=NOW,
        )
        other_assertion_mapping, _issued = self.contract_fixture._assertion(other_envelope_mapping)
        other_assertion = roundtrip.verify_physical_wal_v2_witness_ir_durable_assertion(
            other_assertion_mapping,
            config=self.contract_fixture.config,
            now=NOW,
        )
        with self.assertRaisesRegex(
            ledger.PhysicalWalV2WitnessRoundtripWitnessLedgerError,
            "ROUNDTRIP_COLLISION",
        ):
            self._attest(
                first,
                certificate,
                other_envelope.canonical_envelope,
                other_assertion.canonical_assertion,
            )

    def test_restart_rejects_clock_rollback_after_context_commit(self) -> None:
        runtime = self._runtime()
        self._certify(runtime, self._export_raw())
        with mock.patch.object(ledger, "_host_now", return_value=NOW - timedelta(seconds=1)):
            with self.assertRaisesRegex(
                ledger.PhysicalWalV2WitnessRoundtripWitnessLedgerError,
                "CLOCK_ROLLBACK_DETECTED",
            ):
                ledger.open_physical_wal_v2_witness_roundtrip_witness_ledger(self.config)

    def test_idempotent_retry_with_changed_live_activation_never_releases_old_artifact(self) -> None:
        runtime = self._runtime()
        context = self._certify(runtime, self._export_raw())
        certificate = context.witness_context_certificate
        assert certificate is not None
        envelope, assertion = self._envelope_and_assertion(certificate)
        final = self._attest(runtime, certificate, envelope, assertion)
        self.assertIsNotNone(final.witness_roundtrip_attestation)

        flipped = replace(self.contract_fixture.live, activation_mode="unexpected-promoted-writer")
        with self.assertRaisesRegex(
            ledger.PhysicalWalV2WitnessRoundtripWitnessLedgerError,
            "POST_COMMIT_LIVE_CHANGED",
        ):
            self._attest(runtime, certificate, envelope, assertion, live=flipped)
