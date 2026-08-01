"""Focused fail-closed tests for the local strict-runtime attestation ledger."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import core.physical_durable_replay_attestation_ledger as ledger
import core.physical_postgres_strict_runtime_installation_gate as gate
from tests import test_physical_postgres_strict_runtime_installation_gate as gate_tests


NOW = gate_tests.NOW


@unittest.skipUnless(os.geteuid() == 0, "root-only ledger tests require root")
class PhysicalDurableReplayAttestationLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temporary.name, "state")
        self.state_root.mkdir(mode=0o700)
        os.chmod(self.state_root, 0o700)

        # Reuse the established strict-gate fixture so this test exercises the
        # real opaque request and real four-attestation revalidation path.
        self.gate_fixture = gate_tests.PhysicalPostgresStrictRuntimeInstallationGateTests(
            methodName="runTest"
        )
        self.gate_fixture.setUp()
        self.request, self.inspector = self.gate_fixture._scenario()
        with patch.object(gate.os, "geteuid", return_value=0):
            self.installations = gate.verify_physical_postgres_strict_runtime_installations(
                config=self.gate_fixture._config(self.request),
                inspector=self.inspector,
                now=NOW,
            )
        self.expectation = (
            ledger.build_physical_durable_replay_attestation_ledger_expectation(
                self.request
            )
        )
        self.config = ledger.PhysicalDurableReplayAttestationLedgerConfig(
            state_root=self.state_root,
            expectation=self.expectation,
            enabled=True,
        )
        self.payloads = {
            component: self.inspector.records[component].payload
            for component in gate.STRICT_DURABLE_REPLAY_COMPONENTS
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _record_id(component: str) -> str:
        return f"durable-attestation-{component}-0001"

    @property
    def ledger_path(self) -> Path:
        return self.state_root / "strict-durable-replay-attestation-ledger.json"

    def _append(
        self,
        component: str,
        *,
        record_id: str | None = None,
        payload: bytes | None = None,
    ):
        return ledger.append_physical_durable_replay_attestation(
            config=self.config,
            verified_strict_installations=self.installations,
            component=component,
            record_id=record_id or self._record_id(component),
            attestation_payload=(
                payload if payload is not None else self.payloads[component]
            ),
            now=NOW,
        )

    def _complete(
        self,
    ) -> tuple[ledger.RecordedPhysicalDurableReplayAttestation, ...]:
        return tuple(
            self._append(component)
            for component in gate.STRICT_DURABLE_REPLAY_COMPONENTS
        )

    def test_default_off_non_root_and_unsafe_state_root_fail_before_writing(self) -> None:
        disabled = replace(self.config, enabled=False)
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_DISABLED",
        ):
            ledger.append_physical_durable_replay_attestation(
                config=disabled,
                verified_strict_installations=self.installations,
                component=gate.STRICT_DURABLE_REPLAY_COMPONENTS[0],
                record_id=self._record_id(gate.STRICT_DURABLE_REPLAY_COMPONENTS[0]),
                attestation_payload=self.payloads[gate.STRICT_DURABLE_REPLAY_COMPONENTS[0]],
                now=NOW,
            )
        self.assertFalse(self.ledger_path.exists())

        with patch.object(ledger.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_ROOT_RUNTIME_REQUIRED",
        ):
            self._append(gate.STRICT_DURABLE_REPLAY_COMPONENTS[0])
        self.assertFalse(self.ledger_path.exists())

        os.chmod(self.state_root, 0o755)
        try:
            with self.assertRaisesRegex(
                ledger.PhysicalDurableReplayAttestationLedgerError,
                "DURABLE_REPLAY_LEDGER_STATE_ROOT_UNSAFE",
            ):
                self._append(gate.STRICT_DURABLE_REPLAY_COMPONENTS[0])
        finally:
            os.chmod(self.state_root, 0o700)

    def test_expectation_is_bound_to_real_strict_request_campaign_release_term_and_phase(
        self,
    ) -> None:
        self.assertEqual(
            self.request.campaign_id,
            self.expectation.strict_installation_request.campaign_id,
        )
        self.assertEqual(
            self.request.release_sha,
            self.expectation.strict_installation_request.release_sha,
        )
        self.assertEqual(
            self.request.writer_term_sha256,
            self.expectation.strict_installation_request.writer_term_sha256,
        )
        self.assertEqual(
            ledger.PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_PHASE,
            self.expectation.phase,
        )
        self.assertEqual(
            tuple(gate.STRICT_DURABLE_REPLAY_COMPONENTS),
            tuple(
                component
                for component, _digest in self.expectation.expected_attestation_sha256es
            ),
        )
        self.assertTrue(self.expectation.expectation_binding_sha256)

        tampered = replace(self.expectation, phase="wrong-phase")
        object.__setattr__(tampered, "_capability", self.expectation._capability)
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_EXPECTATION_INVALID",
        ):
            ledger.append_physical_durable_replay_attestation(
                config=replace(self.config, expectation=tampered),
                verified_strict_installations=self.installations,
                component=gate.STRICT_DURABLE_REPLAY_COMPONENTS[0],
                record_id=self._record_id(gate.STRICT_DURABLE_REPLAY_COMPONENTS[0]),
                attestation_payload=self.payloads[gate.STRICT_DURABLE_REPLAY_COMPONENTS[0]],
                now=NOW,
            )

    def test_complete_chain_is_crash_safe_hash_only_and_non_authorizing(self) -> None:
        records = self._complete()
        self.assertEqual(4, len(records))
        self.assertTrue(all(record.not_a_launch_authorization for record in records))
        self.assertTrue(self.ledger_path.is_file())
        info = os.lstat(self.ledger_path)
        self.assertEqual(0, info.st_uid)
        self.assertEqual(0o600, info.st_mode & 0o777)
        raw = self.ledger_path.read_bytes()
        marker = self.payloads[gate.STRICT_DURABLE_REPLAY_COMPONENTS[0]]
        self.assertNotIn(marker, raw)
        self.assertIn(
            hashlib.sha256(marker).hexdigest().encode("ascii"), raw
        )
        parsed = json.loads(raw)
        self.assertEqual(4, len(parsed["entries"]))
        self.assertEqual(
            [1, 2, 3, 4], [item["sequence"] for item in parsed["entries"]]
        )
        self.assertIsNone(parsed["entries"][0]["previous_entry_sha256"])
        self.assertEqual(
            parsed["entries"][0]["entry_sha256"],
            parsed["entries"][1]["previous_entry_sha256"],
        )

        observed = ledger.verify_physical_durable_replay_attestation_ledger(
            config=self.config,
            verified_strict_installations=self.installations,
            now=NOW,
        )
        self.assertTrue(observed.strict_rendering_still_refused_by_scaffold)
        self.assertTrue(observed.not_a_launch_authorization)
        self.assertNotIn("secret", repr(observed).lower())
        self.assertIs(
            observed,
            ledger.require_verified_physical_durable_replay_attestation_ledger(
                observed,
                config=self.config,
                verified_strict_installations=self.installations,
                now=NOW,
            ),
        )

    def test_unverified_or_stale_strict_gate_observation_cannot_write(self) -> None:
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_STRICT_INSTALLATIONS_UNVERIFIED_OR_STALE",
        ):
            ledger.append_physical_durable_replay_attestation(
                config=self.config,
                verified_strict_installations=object(),
                component=gate.STRICT_DURABLE_REPLAY_COMPONENTS[0],
                record_id=self._record_id(gate.STRICT_DURABLE_REPLAY_COMPONENTS[0]),
                attestation_payload=self.payloads[gate.STRICT_DURABLE_REPLAY_COMPONENTS[0]],
                now=NOW,
            )
        self.assertFalse(self.ledger_path.exists())

        # A fresh legitimate write is allowed; the next call proves that the
        # same gate observation cannot be stretched past its expiry window.
        self._append(gate.STRICT_DURABLE_REPLAY_COMPONENTS[0])
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_STRICT_INSTALLATIONS_UNVERIFIED_OR_STALE",
        ):
            ledger.append_physical_durable_replay_attestation(
                config=self.config,
                verified_strict_installations=self.installations,
                component=gate.STRICT_DURABLE_REPLAY_COMPONENTS[1],
                record_id=self._record_id(gate.STRICT_DURABLE_REPLAY_COMPONENTS[1]),
                attestation_payload=self.payloads[gate.STRICT_DURABLE_REPLAY_COMPONENTS[1]],
                now=NOW + timedelta(seconds=61),
            )

    def test_component_and_record_replays_are_rejected_after_durable_append(self) -> None:
        first = gate.STRICT_DURABLE_REPLAY_COMPONENTS[0]
        self._append(first)
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_COMPONENT_ORDER_INVALID",
        ):
            self._append(first, record_id="durable-attestation-retry-0001")
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_RECORD_ID_REPLAYED",
        ):
            self._append(
                gate.STRICT_DURABLE_REPLAY_COMPONENTS[1],
                record_id=self._record_id(first),
            )
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_COMPONENT_ORDER_INVALID",
        ):
            self._append(gate.STRICT_DURABLE_REPLAY_COMPONENTS[3])

    def test_wrong_payload_wrong_order_and_duplicate_expected_hash_fail_closed(self) -> None:
        first, second = gate.STRICT_DURABLE_REPLAY_COMPONENTS[:2]
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_ATTESTATION_HASH_MISMATCH",
        ):
            self._append(first, payload=b"not-the-attestation")
        self.assertFalse(self.ledger_path.exists())
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_COMPONENT_ORDER_INVALID",
        ):
            self._append(second)

        duplicate_hashes = list(self.expectation.expected_attestation_sha256es)
        duplicate_hashes[1] = duplicate_hashes[0]
        tampered = replace(
            self.expectation,
            expected_attestation_sha256es=tuple(duplicate_hashes),
        )
        object.__setattr__(tampered, "_capability", self.expectation._capability)
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            (
                "DURABLE_REPLAY_LEDGER_EXPECTATION_INVALID|"
                "DURABLE_REPLAY_LEDGER_EXPECTED_ATTESTATION_REUSED"
            ),
        ):
            ledger.append_physical_durable_replay_attestation(
                config=replace(self.config, expectation=tampered),
                verified_strict_installations=self.installations,
                component=first,
                record_id="durable-attestation-duplicate-0001",
                attestation_payload=self.payloads[first],
                now=NOW,
            )

    def test_tampered_chain_mode_and_symlink_are_rejected(self) -> None:
        self._complete()
        raw = json.loads(self.ledger_path.read_bytes())
        raw["entries"][2]["previous_entry_sha256"] = "a" * 64
        self.ledger_path.write_bytes(ledger._canonical(raw, code="TEST"))
        os.chmod(self.ledger_path, 0o600)
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_ENTRY_CHAIN_MISMATCH",
        ):
            ledger.verify_physical_durable_replay_attestation_ledger(
                config=self.config,
                verified_strict_installations=self.installations,
                now=NOW,
            )

        # Recreate a valid ledger, then prove a permissive state-file mode is
        # rejected before its contents are parsed or trusted.
        self.ledger_path.unlink()
        self._complete()
        os.chmod(self.ledger_path, 0o640)
        try:
            with self.assertRaisesRegex(
                ledger.PhysicalDurableReplayAttestationLedgerError,
                "DURABLE_REPLAY_LEDGER_STATE_UNSAFE",
            ):
                ledger.verify_physical_durable_replay_attestation_ledger(
                    config=self.config,
                    verified_strict_installations=self.installations,
                    now=NOW,
                )
        finally:
            os.chmod(self.ledger_path, 0o600)

        self.ledger_path.unlink()
        self.ledger_path.symlink_to(self.state_root / "missing")
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_STATE_OPEN_FAILED",
        ):
            ledger.verify_physical_durable_replay_attestation_ledger(
                config=self.config,
                verified_strict_installations=self.installations,
                now=NOW,
            )

    def test_atomic_rename_failure_preserves_prior_committed_state(self) -> None:
        self._append(gate.STRICT_DURABLE_REPLAY_COMPONENTS[0])
        before = self.ledger_path.read_bytes()
        with patch.object(
            ledger.os, "replace", side_effect=OSError("test rename failure")
        ), self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_ATOMIC_RENAME_FAILED",
        ):
            self._append(gate.STRICT_DURABLE_REPLAY_COMPONENTS[1])
        self.assertEqual(before, self.ledger_path.read_bytes())
        self.assertEqual([], list(self.state_root.glob("*.tmp")))

    def test_file_fsync_failure_never_replaces_prior_state(self) -> None:
        with patch.object(
            ledger.os, "fsync", side_effect=OSError("test fsync failure")
        ), self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_TEMPORARY_FSYNC_FAILED",
        ):
            self._append(gate.STRICT_DURABLE_REPLAY_COMPONENTS[0])
        self.assertFalse(self.ledger_path.exists())
        self.assertEqual([], list(self.state_root.glob("*.tmp")))

    def test_completed_ledger_cannot_be_relabelled_or_replayed(self) -> None:
        self._complete()
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_COMPLETE",
        ):
            self._append(
                gate.STRICT_DURABLE_REPLAY_COMPONENTS[0],
                record_id="durable-attestation-after-complete-0001",
            )
        observed = ledger.verify_physical_durable_replay_attestation_ledger(
            config=self.config,
            verified_strict_installations=self.installations,
            now=NOW,
        )
        relabelled = replace(observed, strict_rendering_still_refused_by_scaffold=False)
        object.__setattr__(relabelled, "_capability", observed._capability)
        with self.assertRaisesRegex(
            ledger.PhysicalDurableReplayAttestationLedgerError,
            "DURABLE_REPLAY_LEDGER_VERIFIED_RESULT_INVALID",
        ):
            ledger.require_verified_physical_durable_replay_attestation_ledger(
                relabelled,
                config=self.config,
                verified_strict_installations=self.installations,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
