"""No-network tests for the separate WA-IR reverse capture bridge."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_ir_to_fi_object_storage_failback_preflight as preflight
from core import physical_wa_ir_postgres_failback_capture_bridge as bridge
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_wal_base_backup_spool import (
    PhysicalWalBaseBackupManifestBinding,
    PhysicalWalBaseBackupUploadReceipt,
)
from tests.physical_arvan_s3_four_role_fixture import make_four_role_fixture
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "ir-fi-capture-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT = "age1" + "c" * 30


def _sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


def _term(*, now: datetime = NOW):
    signer = Ed25519PrivateKey.generate()
    proof = build_object_delta_role_matrix_witnessed_term_proof(
        holder_site="webapp_ir",
        writer_epoch=51,
        writer_lease_id="ir-capture-writer-lease-51",
        witness_transition_id="ir-capture-transition-51",
        issued_at=now - timedelta(seconds=10),
        expires_at=now + timedelta(seconds=100),
        witness_signer=signer,
    )
    public_key = signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return verify_object_delta_role_matrix_witnessed_term(
        proof,
        witness_public_key=public_key,
        maximum_lease_duration_seconds=120,
        safety_margin_seconds=5,
        now=now,
    )


class _Uploader:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def upload(self, *, snapshot_path: Path, descriptor_bytes: bytes, descriptor_sha256: str):
        parsed = json.loads(descriptor_bytes.decode("ascii"))
        self.calls.append(
            {
                "snapshot_path": snapshot_path,
                "descriptor_sha256": descriptor_sha256,
                "descriptor": parsed,
            }
        )
        return PhysicalWalBaseBackupUploadReceipt(
            descriptor_sha256=descriptor_sha256,
            object_key=parsed["object_key"],
            version_id="reverse-base-version-001",
            ciphertext_sha256="a" * 64,
            ciphertext_bytes=snapshot_path.stat().st_size + 32,
            encryption="age-v1",
            age_recipient=parsed["destination_age_recipient"],
            immutability="versioned_create_only_readback_v1",
        )


class _ReverseHandoff:
    def __init__(self, uploader: _Uploader) -> None:
        self.uploader = uploader
        self.terms: list[object] = []

    def base_backup_uploader(self, *, current_witnessed_term):
        self.terms.append(current_witnessed_term)
        return self.uploader


class _Runner:
    def __init__(self, *, content: bytes, reported_sha: str | None = None) -> None:
        self.content = content
        self.reported_sha = reported_sha
        self.calls: list[bridge.PhysicalWaIrPostgresFailbackCaptureInvocation] = []

    def capture_consistent_failback_base_backup(self, *, invocation):
        self.calls.append(invocation)
        name = "ir-failback-base-backup.tar"
        path = invocation.capture_root / name
        path.write_bytes(self.content)
        path.chmod(0o600)
        return bridge.PhysicalWaIrPostgresFailbackCaptureArtifact(
            artifact_name=name,
            plaintext_sha256=self.reported_sha or _sha(self.content),
            plaintext_bytes=len(self.content),
            completion_attestation_sha256="b" * 64,
        )


@unittest.skipUnless(os.geteuid() == 0, "capture bridge is root-only")
class PhysicalWaIrPostgresFailbackCaptureBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="wa-ir-failback-capture-")
        self.root = Path(self.temp.name).resolve()
        self.capture_root = self.root / "capture"
        self.spool_root = self.root / "spool"
        for value in (self.capture_root, self.spool_root):
            value.mkdir(mode=0o700)
            value.chmod(0o700)
        self.term = _term()
        self.four_role_fixture = make_four_role_fixture(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            fi_publisher_identity_sha256="1" * 64,
            ir_receiver_identity_sha256="2" * 64,
            ir_publisher_identity_sha256="3" * 64,
            fi_receiver_identity_sha256="4" * 64,
        )
        self.binding = self.four_role_fixture.binding
        self.live_iam = make_four_role_live_iam_durable_admission_fixture(
            binding=self.binding,
            observed_at=NOW,
        )
        observed = preflight.build_physical_ir_to_fi_object_storage_failback_observation(
            binding=self.binding,
            four_role_projection_binding=self.four_role_fixture.verified_binding,
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
            observed_at=NOW,
        )
        self.preflight = preflight.verify_physical_ir_to_fi_object_storage_failback_preflight(
            observed,
            binding=self.binding,
            four_role_projection_binding=self.four_role_fixture.verified_binding,
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
            now=NOW,
        )
        self.preflight_config = self.four_role_fixture.preflight_config(
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
        )
        self.manifest = PhysicalWalBaseBackupManifestBinding(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            baseline_generation_id="ir-fi-capture-generation-20260731",
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=16 * 1024 * 1024,
            baseline_wal_lsn="0/1800000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/2800000",
            destination_age_recipient=RECIPIENT,
            object_storage_namespace="physical-failback",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _config(self, handoff, **changes: object):
        fields: dict[str, object] = {
            "reverse_handoff": handoff,
            "preflight_config": self.preflight_config,
            "preflight": self.preflight,
            "manifest_binding": self.manifest,
            "capture_root": self.capture_root,
            "spool_root": self.spool_root,
            "maximum_base_backup_bytes": 1024 * 1024,
            "spool_reserve_bytes": 1,
            "enabled": True,
        }
        fields.update(changes)
        return bridge.RootOwnedWaIrPostgresFailbackCaptureBridgeConfig(**fields)

    def _run(self, config, runner):
        return bridge.run_root_owned_wa_ir_postgres_failback_capture_bridge(
            config=config,
            current_witnessed_term=self.term,
            runner=runner,
            now=NOW,
        )

    def test_local_ir_capture_flows_only_to_reverse_handoff(self) -> None:
        uploader = _Uploader()
        handoff = _ReverseHandoff(uploader)
        runner = _Runner(content=b"ir-consistent-base-backup" * 20)

        result = self._run(self._config(handoff), runner)

        self.assertEqual(
            bridge.PHYSICAL_WA_IR_POSTGRES_FAILBACK_CAPTURE_BRIDGE_STATUS,
            result.status,
        )
        self.assertEqual((), result.reason_codes)
        self.assertEqual(1, len(runner.calls))
        self.assertEqual(1, len(handoff.terms))
        self.assertEqual(1, len(uploader.calls))
        descriptor = uploader.calls[0]["descriptor"]
        assert isinstance(descriptor, dict)
        self.assertEqual("physical-failback", descriptor["object_storage_namespace"])
        self.assertEqual("webapp_ir", descriptor["source_site"])
        self.assertEqual("webapp_fi", descriptor["destination_site"])
        self.assertTrue(str(descriptor["object_key"]).startswith("physical-failback/"))
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.writer_authorized)
        self.assertFalse(result.traffic_switch_authorized)
        self.assertFalse(result.full_matrix_authorized)

    def test_disabled_or_normal_namespace_blocks_before_runner_and_handoff(self) -> None:
        uploader = _Uploader()
        handoff = _ReverseHandoff(uploader)
        runner = _Runner(content=b"capture")
        result = self._run(self._config(handoff, enabled=False), runner)
        self.assertEqual(("WA_IR_FAILBACK_CAPTURE_DISABLED",), result.reason_codes)
        self.assertEqual([], runner.calls)
        self.assertEqual([], handoff.terms)

        normal = replace(self.manifest, object_storage_namespace="physical-wal")
        result = self._run(self._config(handoff, manifest_binding=normal), runner)
        self.assertEqual(("WA_IR_FAILBACK_CAPTURE_ROUTE_BINDING_MISMATCH",), result.reason_codes)
        self.assertEqual([], runner.calls)
        self.assertEqual([], handoff.terms)
        self.assertEqual([], uploader.calls)

    def test_runner_hash_forgery_blocks_before_reverse_uploader(self) -> None:
        uploader = _Uploader()
        handoff = _ReverseHandoff(uploader)
        runner = _Runner(content=b"actual", reported_sha="f" * 64)
        result = self._run(self._config(handoff), runner)
        self.assertEqual(("WA_IR_FAILBACK_CAPTURE_ARTIFACT_UNSAFE",), result.reason_codes)
        self.assertEqual(1, len(runner.calls))
        self.assertEqual([], handoff.terms)
        self.assertEqual([], uploader.calls)

    def test_source_has_no_normal_capture_or_execution_surface(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core/physical_wa_ir_postgres_failback_capture_bridge.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "physical_wa_fi_postgres_helper_capture_bridge",
            "physical_wa_fi_postgres_object_storage_handoff_runtime",
            "physical_wa_ir_postgres_recovery_materialization_runtime",
            "import subprocess",
            "from subprocess",
            "import socket",
            "from socket",
            "import docker",
            "from docker",
            "import psycopg",
            "from psycopg",
        )
        self.assertFalse([item for item in forbidden if item in source])


if __name__ == "__main__":
    unittest.main()
