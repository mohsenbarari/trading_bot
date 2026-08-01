from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta
import hashlib
import inspect
import json
from pathlib import Path
from unittest import TestCase

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.physical_postgres_recovery_preflight import (
    PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED,
    assess_physical_postgres_recovery_preflight,
)
from core.physical_postgres_recovery_readback_collector import (
    PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_DEFAULT_ENABLED,
    PhysicalPostgresRecoveryLocalInspection,
    PhysicalPostgresRecoveryReadbackCollectorError,
    PhysicalPostgresRecoveryReadbackLocalInspector,
    PhysicalPostgresRecoveryReadbackRootConfig,
    collect_physical_postgres_recovery_receiver_readback,
)
from core.physical_postgres_recovery_preflight import PhysicalPostgresRecoveryPreflightBinding
from tests import test_physical_postgres_recovery_preflight as recovery_preflight_tests


NOW = recovery_preflight_tests.NOW


class _BoundLocalInspector:
    """A local-only double for the narrow fixed inspector contract."""

    def __init__(self, **overrides: object) -> None:
        self.overrides = overrides
        self.calls: list[object] = []

    def inspect_bound_recovery_receiver(self, *, request: object):
        self.calls.append(request)
        values: dict[str, object] = {
            "observed_at": NOW,
            "receiver_site": request.receiver_site,
            "source_site": request.source_site,
            "destination_site": request.destination_site,
            "stage_bundle_id": request.stage_bundle_id,
            "stage_receipt_sha256": request.stage_receipt_sha256,
            "route_binding_sha256": request.route_binding_sha256,
            "bundle_terminal_wal_lsn": request.bundle_terminal_wal_lsn,
            "writer_holder_site": request.writer_holder_site,
            "writer_epoch": request.writer_epoch,
            "writer_lease_id": request.writer_lease_id,
            "witness_transition_id": request.witness_transition_id,
            "witnessed_term_proof_sha256": request.witnessed_term_proof_sha256,
            "in_recovery": True,
            "role": "standby",
            "database_system_identifier": request.database_system_identifier,
            "timeline_id": request.timeline_id,
            "wal_segment_size_bytes": request.wal_segment_size_bytes,
            "baseline_generation_id": request.baseline_generation_id,
            "replay_lsn": request.bundle_terminal_wal_lsn,
        }
        values.update(self.overrides)
        return PhysicalPostgresRecoveryLocalInspection(**values)


class PhysicalPostgresRecoveryReadbackCollectorTests(TestCase):
    def setUp(self) -> None:
        self.fixture = recovery_preflight_tests.PhysicalPostgresRecoveryPreflightTests(
            methodName="runTest"
        )
        self.fixture.setUp()
        self.bundle, self.term = self.fixture.bundle()
        self.stage = self.fixture.stage_binding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
        )
        self.binding = self.fixture.binding(
            local_standby_site="webapp_ir",
            term=self.term,
            stage=self.stage,
        )
        self.config = PhysicalPostgresRecoveryReadbackRootConfig(
            enabled=True,
            source_site="webapp_fi",
            receiver_site="webapp_ir",
            stage_bundle_id=self.stage.bundle_id,
            stage_receipt_sha256=self.stage.stage_receipt_sha256,
            route_binding_sha256=self.stage.route_binding_sha256,
        )

    def collect(self, inspector: object, **overrides: object):
        values: dict[str, object] = {
            "root_config": self.config,
            "bundle": self.bundle,
            "binding": self.binding,
            "current_witnessed_term": self.term,
            "inspector": inspector,
            "now": NOW,
        }
        values.update(overrides)
        return collect_physical_postgres_recovery_receiver_readback(**values)

    def test_exact_safe_readback_is_canonical_hashed_and_consumable_by_preflight(self) -> None:
        inspector = _BoundLocalInspector()
        evidence = self.collect(inspector)
        payload = json.loads(evidence.raw_evidence)

        self.assertFalse(PHYSICAL_POSTGRES_RECOVERY_READBACK_COLLECTOR_DEFAULT_ENABLED)
        self.assertEqual(1, len(inspector.calls))
        self.assertEqual(
            hashlib.sha256(evidence.raw_evidence).hexdigest(),
            evidence.evidence_sha256,
        )
        self.assertEqual(canonical_json_bytes(payload), evidence.raw_evidence)
        self.assertEqual(
            {
                "schema",
                "status",
                "observed_at",
                "receiver_site",
                "source_site",
                "destination_site",
                "stage_bundle_id",
                "stage_receipt_sha256",
                "route_binding_sha256",
                "manifest_sha256es",
                "object_versions",
                "base_backup_manifest_sha256",
                "bundle_terminal_wal_lsn",
                "writer_term",
                "postgresql",
            },
            set(payload),
        )
        self.assertEqual("webapp_fi", payload["source_site"])
        self.assertEqual("webapp_ir", payload["destination_site"])
        self.assertEqual(self.stage.stage_receipt_sha256, payload["stage_receipt_sha256"])
        self.assertEqual(self.bundle.terminal_wal_lsn, payload["bundle_terminal_wal_lsn"])
        self.assertEqual(self.term.proof_sha256, payload["writer_term"]["witnessed_term_proof_sha256"])
        self.assertEqual(True, payload["postgresql"]["in_recovery"])
        self.assertEqual("standby", payload["postgresql"]["role"])
        self.assertEqual(self.bundle.terminal_wal_lsn, payload["postgresql"]["replay_lsn"])

        result = assess_physical_postgres_recovery_preflight(
            bundle=self.bundle,
            binding=self.binding,
            receiver_readback_evidence=evidence,
            now=NOW,
        )
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED, result.status)
        self.assertEqual((), result.reason_codes)

    def test_replay_below_terminal_is_explicitly_staged_not_replay_verified(self) -> None:
        inspector = _BoundLocalInspector(replay_lsn="0/2000000")
        evidence = self.collect(inspector)
        payload = json.loads(evidence.raw_evidence)
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED, payload["status"])

        result = assess_physical_postgres_recovery_preflight(
            bundle=self.bundle,
            binding=self.binding,
            receiver_readback_evidence=evidence,
            now=NOW,
        )
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_STAGED_NOT_REPLAY_VERIFIED, result.status)
        self.assertEqual(("REPLAY_EVIDENCE_NOT_OBSERVED",), result.reason_codes)

    def test_disabled_or_unsafe_root_config_fails_before_any_inspector_call(self) -> None:
        cases = (
            (
                PhysicalPostgresRecoveryReadbackRootConfig(),
                "RECOVERY_READBACK_COLLECTOR_DISABLED",
            ),
            (replace(self.config, root_owner_uid=1000), "RECOVERY_READBACK_ROOT_CONFIG_NOT_ROOT"),
            (
                replace(self.config, inspection_contract="caller-command"),
                "RECOVERY_READBACK_ROOT_CONFIG_INSPECTION_CONTRACT_INVALID",
            ),
            (
                replace(self.config, receiver_role="primary"),
                "RECOVERY_READBACK_ROOT_CONFIG_RECEIVER_ROLE_INVALID",
            ),
            (
                replace(self.config, maximum_evidence_age_seconds=True),
                "RECOVERY_READBACK_ROOT_CONFIG_AGE_INVALID",
            ),
        )
        for config, code in cases:
            with self.subTest(code=code):
                inspector = _BoundLocalInspector()
                with self.assertRaisesRegex(PhysicalPostgresRecoveryReadbackCollectorError, code):
                    self.collect(inspector, root_config=config)
                self.assertEqual([], inspector.calls)

    def test_stage_bundle_and_current_term_pins_are_revalidated_before_inspection(self) -> None:
        other_term = self.fixture.witnessed_term(holder_site="webapp_fi", epoch=8)
        bad_binding = PhysicalPostgresRecoveryPreflightBinding(
            local_standby_site="webapp_fi",
            stage_binding=self.stage,
            expected_witnessed_term=self.term,
        )
        cases = (
            (
                {"root_config": replace(self.config, stage_receipt_sha256="f" * 64)},
                "RECOVERY_READBACK_STAGE_PIN_MISMATCH",
            ),
            (
                {"current_witnessed_term": other_term},
                "RECOVERY_READBACK_CURRENT_TERM_MISMATCH",
            ),
            (
                {"binding": bad_binding},
                "RECOVERY_READBACK_ROOT_CONFIG_ROUTE_PIN_MISMATCH",
            ),
        )
        for overrides, code in cases:
            with self.subTest(code=code):
                inspector = _BoundLocalInspector()
                with self.assertRaisesRegex(PhysicalPostgresRecoveryReadbackCollectorError, code):
                    self.collect(inspector, **overrides)
                self.assertEqual([], inspector.calls)

    def test_stale_invalid_boolean_and_tampered_bundle_fail_closed(self) -> None:
        cases = (
            (
                _BoundLocalInspector(observed_at=NOW - timedelta(seconds=91)),
                {},
                "LOCAL_INSPECTION_TIME_STALE",
                1,
            ),
            (
                _BoundLocalInspector(in_recovery=1),
                {},
                "LOCAL_INSPECTION_RECOVERY_STATE_INVALID",
                1,
            ),
            (
                _BoundLocalInspector(),
                {"bundle": replace(self.bundle, terminal_wal_lsn="0/4000000")},
                "RECOVERY_READBACK_BUNDLE_UNVERIFIED",
                0,
            ),
        )
        for inspector, overrides, code, expected_calls in cases:
            with self.subTest(code=code):
                if "bundle" in overrides:
                    object.__setattr__(overrides["bundle"], "_capability", self.bundle._capability)
                with self.assertRaisesRegex(PhysicalPostgresRecoveryReadbackCollectorError, code):
                    self.collect(inspector, **overrides)
                self.assertEqual(expected_calls, len(inspector.calls))

    def test_wrong_route_terminal_lsn_and_term_from_inspector_are_rejected(self) -> None:
        cases = (
            (
                _BoundLocalInspector(source_site="webapp_ir"),
                "LOCAL_INSPECTION_ROUTE_OR_STAGE_MISMATCH",
            ),
            (
                _BoundLocalInspector(bundle_terminal_wal_lsn="0/4000000"),
                "LOCAL_INSPECTION_TERMINAL_LSN_MISMATCH",
            ),
            (
                _BoundLocalInspector(writer_epoch=8),
                "LOCAL_INSPECTION_TERM_MISMATCH",
            ),
        )
        for inspector, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(PhysicalPostgresRecoveryReadbackCollectorError, code):
                    self.collect(inspector)
                self.assertEqual(1, len(inspector.calls))

    def test_evidence_hash_tamper_is_rejected_by_the_existing_recovery_preflight(self) -> None:
        evidence = self.collect(_BoundLocalInspector())
        tampered = replace(evidence, evidence_sha256="f" * 64)
        result = assess_physical_postgres_recovery_preflight(
            bundle=self.bundle,
            binding=self.binding,
            receiver_readback_evidence=tampered,
            now=NOW,
        )
        self.assertEqual(PHYSICAL_POSTGRES_RECOVERY_STATUS_BLOCKED, result.status)
        self.assertEqual(("RECEIVER_EVIDENCE_HASH_MISMATCH",), result.reason_codes)

    def test_inspector_surface_has_no_caller_command_path_or_environment_and_module_has_no_io(self) -> None:
        config_fields = {item.name for item in fields(PhysicalPostgresRecoveryReadbackRootConfig)}
        self.assertFalse(config_fields.intersection({"command", "sql", "path", "environment", "env", "url"}))
        signature = inspect.signature(
            PhysicalPostgresRecoveryReadbackLocalInspector.inspect_bound_recovery_receiver
        )
        self.assertEqual(("self", "request"), tuple(signature.parameters))

        source = (
            Path(__file__).resolve().parents[1]
            / "core/physical_postgres_recovery_readback_collector.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "import os",
            "from os",
            "import socket",
            "from socket",
            "import subprocess",
            "from subprocess",
            "import sqlalchemy",
            "from sqlalchemy",
            "import requests",
            "from requests",
            "import httpx",
            "from httpx",
            "import boto3",
            "from boto3",
            "import docker",
            "from docker",
        )
        self.assertFalse([item for item in forbidden if item in source])


if __name__ == "__main__":
    import unittest

    unittest.main()
