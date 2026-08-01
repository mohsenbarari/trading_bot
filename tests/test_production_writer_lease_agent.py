from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from core.production_snapshot_promotion import (
    build_promotion_proof,
    canonical_json_bytes,
    parse_restore_receipt,
)
from core.production_writer_lease import load_production_writer_lease
from scripts import production_writer_lease_agent as agent


RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
APP_IMAGE_ID = "sha256:" + "c" * 64


def _write_private(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    os.chmod(path, 0o600)


def _restore_receipt(*, source_site: str, destination_site: str, published_at: datetime) -> dict:
    database = {
        "sha256": "a" * 64,
        "bytes": 11,
        "object_key": "campaign/snapshot/database.age",
        "version_id": "database-version-1",
        "ciphertext_sha256": "b" * 64,
        "ciphertext_bytes": 22,
    }
    uploads = {
        "sha256": "c" * 64,
        "bytes": 33,
        "object_key": "campaign/snapshot/uploads.age",
        "version_id": "uploads-version-1",
        "ciphertext_sha256": "d" * 64,
        "ciphertext_bytes": 44,
    }
    payload = {
        "schema": "gold-trade-snapshot-restore-receipt-v1",
        "status": "restored_verified",
        "source_site": source_site,
        "destination_site": destination_site,
        "source_generation": "source-generation-1",
        "snapshot_id": "snapshot-1",
        "release_sha": RELEASE_SHA,
        "alembic_revision": "f2c7d8e9a0b1",
        "source_db_snapshot_started_at": (published_at - timedelta(seconds=2)).isoformat(),
        "source_capture_completed_at": (published_at - timedelta(seconds=1)).isoformat(),
        "published_at": published_at.isoformat(),
        "ready_at": (published_at + timedelta(seconds=1)).isoformat(),
        "restored_at": (published_at + timedelta(seconds=2)).isoformat(),
        "restore_verified_at": (published_at + timedelta(seconds=3)).isoformat(),
        "stage_receipt_sha256": "e" * 64,
        "restored_database_sha256": database["sha256"],
        "restored_uploads_sha256": uploads["sha256"],
        "database": database,
        "uploads": uploads,
        "manifest": {
            "object_key": "campaign/snapshot/manifest.age",
            "version_id": "manifest-version-1",
            "ciphertext_sha256": "f" * 64,
            "ciphertext_bytes": 55,
        },
    }
    payload["receipt_sha256"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return payload


def _proof(*, site: str, epoch: int = 6) -> dict:
    issued_at = datetime.now(timezone.utc)
    return {
        "version": 1,
        "authority": "webapp",
        "holder_site": site,
        "writer_epoch": epoch,
        "lease_id": f"lease-{epoch}",
        "issued_at": issued_at.isoformat(),
        "expires_at": (issued_at + timedelta(seconds=180)).isoformat(),
        "witness_transition_id": f"transition-{epoch}",
        "signature": base64.b64encode(b"x" * 64).decode("ascii"),
    }


def _active_snapshot(*, receipt: dict, receipt_path: Path) -> dict:
    snapshot_id = receipt["snapshot_id"]
    return {
        "schema_version": "gold-trade-snapshot-restore-receipt-v1",
        "status": "ready",
        "source_site": receipt["source_site"],
        "destination_site": receipt["destination_site"],
        "source_generation": receipt["source_generation"],
        "snapshot_id": snapshot_id,
        "release_sha": receipt["release_sha"],
        "alembic_revision": receipt["alembic_revision"],
        "source_db_snapshot_started_at": receipt["source_db_snapshot_started_at"],
        "source_capture_completed_at": receipt["source_capture_completed_at"],
        "published_at": receipt["published_at"],
        "ready_at": receipt["ready_at"],
        "audit": {"status": "verified"},
        "candidate": {
            "generation": snapshot_id,
            "db_volume": f"trading_bot_wa_ir_pg_{snapshot_id}",
            "uploads_volume": f"trading_bot_wa_ir_uploads_{snapshot_id}",
            "audit_volume": f"trading_bot_wa_ir_audit_{snapshot_id}",
            "db_container": f"trading_bot_wa_ir_snapshot_db_{snapshot_id}",
            "compose_project": f"trading_bot_wa_ir_snapshot_{snapshot_id}",
        },
        "witness_restore_receipt": {
            "path": str(receipt_path),
            "receipt_sha256": receipt["receipt_sha256"],
            "stage_receipt_sha256": receipt["stage_receipt_sha256"],
            "source_generation": receipt["source_generation"],
            "snapshot_id": snapshot_id,
        },
    }


def _runtime_binding(*, proof: dict, receipt: dict) -> dict:
    snapshot_id = receipt["snapshot_id"]
    labels = {
        "com.docker.compose.project": agent.WA_IR_PROMOTION_PROJECT_NAME,
    }
    expected_volumes = {
        "db": [f"trading_bot_wa_ir_pg_{snapshot_id}"],
        "redis": [f"trading_bot_wa_ir_redis_{snapshot_id}"],
        "app": sorted(
            [
                f"trading_bot_wa_ir_uploads_{snapshot_id}",
                f"trading_bot_wa_ir_audit_{snapshot_id}",
            ]
        ),
    }
    images = {
        "db": "postgres:15-alpine",
        "redis": "redis:7-alpine",
        "app": "trading-bot:2c08",
    }
    containers = {}
    for service, letter in (("db", "a"), ("redis", "b"), ("app", "c")):
        containers[service] = {
            "container_id": letter * 64,
            "image": images[service],
            "image_id": f"sha256:{letter * 64}",
            "labels_sha256": agent._runtime_binding_hash(
                {**labels, "com.docker.compose.service": service}
            ),
            "volume_names": expected_volumes[service],
            "restart_policy": "no",
        }
    payload = {
        "schema": agent.WA_IR_RUNTIME_BINDING_SCHEMA,
        "promotion_proof_sha256": proof["proof_sha256"],
        "snapshot_id": receipt["snapshot_id"],
        "source_generation": receipt["source_generation"],
        "release_sha": receipt["release_sha"],
        "snapshot_restore_receipt_sha256": receipt["receipt_sha256"],
        "snapshot_stage_receipt_sha256": receipt["stage_receipt_sha256"],
        "epoch": proof["epoch"],
        "lease_id": proof["lease_id"],
        "containers": containers,
    }
    payload["binding_sha256"] = agent._runtime_binding_hash(payload)
    return payload


class ProductionWriterLeaseAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provenance_by_receipt: dict[Path, dict] = {}
        patcher = mock.patch.object(
            agent,
            "load_installed_release_receipt",
            side_effect=lambda path: self.provenance_by_receipt[Path(path)],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _config(
        self,
        directory: Path,
        *,
        site: str,
        mode: str = "writer",
        services: list[str] | None = None,
        lease_duration_seconds: int | None = None,
        safety_margin_seconds: int | None = None,
        renew_interval_seconds: int | None = None,
    ) -> agent.AgentConfig:
        secret = directory / "witness.secret"
        public_key = directory / "witness.pub"
        promotion_env = directory / "promotion.env"
        config_path = directory / "agent.json"
        provenance_receipt = directory / "release-provenance.json"
        application_root = directory / "releases" / RELEASE_SHA
        _write_private(secret, "s" * 32)
        _write_private(public_key, base64.b64encode(b"p" * 32).decode("ascii"))
        is_ir_writer = mode == "writer" and site == "webapp_ir"
        is_fenced_fi_writer = mode == "fenced_fi_writer" and site == "webapp_fi"
        if is_ir_writer:
            application_root.mkdir(parents=True)
            application_root.chmod(0o755)
            _write_private(provenance_receipt, "fixture\n")
            self.provenance_by_receipt[provenance_receipt] = {
                "application": {
                    "release_sha": RELEASE_SHA,
                    "release_root": str(application_root),
                },
                "runtime_images": {"app_image_id": APP_IMAGE_ID},
            }
        _write_private(
            promotion_env,
            "\n".join(
                (
                    "WA_IR_PROMOTION_RUNTIME_ENV_FILE=/root/secure-envs/wa-ir.env",
                    "WA_IR_POSTGRES_IMAGE=postgres:15-alpine",
                    "WA_IR_REDIS_IMAGE=redis:7-alpine",
                    "WA_IR_APP_IMAGE=trading-bot:2c08",
                    f"RELEASE_SHA={RELEASE_SHA}",
                    f"WA_IR_APPLICATION_RELEASE_ROOT={application_root}",
                    "",
                )
            ),
        )
        fenced_env = directory / "fenced-fi.env"
        if is_fenced_fi_writer:
            application_root.mkdir(parents=True, exist_ok=True)
            application_root.chmod(0o755)
            _write_private(
                fenced_env,
                "\n".join(
                    (
                        f"RELEASE_SHA={RELEASE_SHA}",
                        "WA_FI_WRITER_APP_IMAGE=trading-bot:2c08",
                        "WA_FI_WRITER_BOT_IMAGE=trading-bot:2c08",
                        f"WA_FI_WRITER_RUNTIME_ENV_FILE={fenced_env}",
                        f"WA_FI_WRITER_APPLICATION_RELEASE_ROOT={application_root}",
                        f"WA_FI_WRITER_TERM_PARENT_DIRECTORY={directory / 'writer-terms'}",
                        "WA_FI_WRITER_RUNTIME_NETWORK_NAME=trading_bot_fi_runtime",
                        "WA_FI_WRITER_UPLOADS_VOLUME=trading_bot_fi_uploads",
                        "WA_FI_WRITER_AUDIT_VOLUME=trading_bot_fi_audit",
                        "WA_FI_WRITER_APP_LOCAL_PORT=8000",
                        "APPLICATION_WRITER_TERM_SAFETY_MARGIN_SECONDS=15",
                        "APPLICATION_WRITER_TERM_MAX_LEASE_DURATION_SECONDS=60",
                        "",
                    )
                ),
            )
        default_lease_duration = 60 if (is_ir_writer or is_fenced_fi_writer) else 180
        default_safety_margin = 15
        default_renew_interval = 10 if (is_ir_writer or is_fenced_fi_writer) else 30
        config = {
            "schema": agent.AGENT_SCHEMA,
            "mode": mode,
            "site": site,
            "lease_file": (
                str(
                    directory / "writer-terms" / "writer-lease.json"
                    if is_fenced_fi_writer
                    else directory / "writer-lease.json"
                )
                if mode in {"writer", "fenced_fi_writer"}
                else None
            ),
            "runtime": {
                "compose_file": (
                    str(agent.WA_IR_PROMOTED_COMPOSE_FILE)
                    if is_ir_writer
                    else (
                        str(agent.WA_FI_FENCED_WRITER_COMPOSE_FILE)
                        if is_fenced_fi_writer
                        else str(directory / "isolated-compose.yml")
                    )
                ),
                "env_file": (
                    str(promotion_env)
                    if is_ir_writer
                    else str(fenced_env) if is_fenced_fi_writer else None
                ),
                "selection_env_file": (
                    str(directory / "selected-candidate.env")
                    if is_ir_writer
                    else None
                ),
                "services": services if services is not None else (
                    (["db", "redis", "app"] if is_ir_writer else ["app", "sync_worker"])
                    if mode == "writer"
                    else ["app", "bot"] if is_fenced_fi_writer else ["bot", "sync_worker"]
                ),
            },
            "witness": {
                "url": "https://witness.example.test",
                "key_id": "host-key-1",
                "secret_file": str(secret),
                "public_key_file": str(public_key),
                "ca_bundle": None,
                "timeout_seconds": 2,
                "lease_duration_seconds": (
                    default_lease_duration
                    if lease_duration_seconds is None
                    else lease_duration_seconds
                ),
                "safety_margin_seconds": (
                    default_safety_margin
                    if safety_margin_seconds is None
                    else safety_margin_seconds
                ),
                "renew_interval_seconds": (
                    default_renew_interval
                    if renew_interval_seconds is None
                    else renew_interval_seconds
                ),
            },
        }
        if is_ir_writer:
            config["release_provenance"] = {
                "receipt": str(provenance_receipt),
                "application_release_sha": RELEASE_SHA,
                "application_release_root": str(application_root),
            }
        _write_private(config_path, json.dumps(config))
        return agent._load_config(config_path)

    def _existing_activation_fixture(self, directory: Path) -> tuple[
        agent.AgentConfig,
        Path,
        Path,
        Path,
        dict,
    ]:
        config = self._config(directory, site="webapp_ir")
        receipt_path = directory / "restore-receipt.json"
        active_snapshot_path = directory / "active-snapshot.json"
        proof_directory = directory / "promotion-proofs"
        proof_directory.mkdir(mode=0o700)
        receipt = _restore_receipt(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            published_at=datetime.now(timezone.utc) - timedelta(seconds=4),
        )
        _write_private(receipt_path, json.dumps(receipt))
        _write_private(
            active_snapshot_path,
            json.dumps(_active_snapshot(receipt=receipt, receipt_path=receipt_path)),
        )
        snapshot = parse_restore_receipt(receipt, action="promote_ir")
        witness_proof = _proof(site="webapp_ir", epoch=9)
        promotion_proof = build_promotion_proof(
            action="promote_ir",
            operation_id="93877f06-2671-4d78-b1f8-2c79bf759755",
            snapshot=snapshot,
            witness_proof=witness_proof,
        )
        proof_path = agent._automatic_proof_path(
            directory=proof_directory,
            action="promote_ir",
            snapshot_id=snapshot.snapshot_id,
            receipt_sha256=snapshot.receipt_sha256,
        )
        _write_private(proof_path, json.dumps(promotion_proof))
        agent._write_lease(config.lease_file, proof=witness_proof)
        return config, receipt_path, active_snapshot_path, proof_directory, receipt

    def _recovery_fixture(self, directory: Path) -> tuple[
        agent.AgentConfig,
        Path,
        Path,
        Path,
        dict,
    ]:
        config, receipt_path, active_snapshot_path, proof_directory, receipt = (
            self._existing_activation_fixture(directory)
        )
        snapshot = parse_restore_receipt(receipt, action="promote_ir")
        proof_path = agent._automatic_proof_path(
            directory=proof_directory,
            action="promote_ir",
            snapshot_id=snapshot.snapshot_id,
            receipt_sha256=snapshot.receipt_sha256,
        )
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        _write_private(
            agent._runtime_binding_path(proof_path),
            json.dumps(_runtime_binding(proof=proof, receipt=receipt)),
        )
        return config, receipt_path, active_snapshot_path, proof_path, receipt

    def test_generic_webapp_fi_writer_is_rejected_before_runtime_selection(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "generic WebApp-FI writer mode is retired"):
                self._config(Path(raw), site="webapp_fi", mode="writer", services=["app", "sync_worker"])

    def test_fenced_fi_config_requires_the_pinned_compose_and_exact_app_bot_scope(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "fenced WebApp-FI writer must manage only app and bot",
            ):
                self._config(
                    directory,
                    site="webapp_fi",
                    mode="fenced_fi_writer",
                    services=["app", "sync_worker"],
                )

            self._config(directory, site="webapp_fi", mode="fenced_fi_writer")
            config_path = directory / "agent.json"
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["runtime"]["compose_file"] = str(directory / "arbitrary-compose.yml")
            _write_private(config_path, json.dumps(payload))
            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "pinned isolated writer compose file",
            ):
                agent._load_config(config_path)

    def test_fenced_fi_compose_uses_fixed_project_profile_and_no_pull_or_build(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="fenced_fi_writer")
            with mock.patch.object(agent, "_run_runtime_command", return_value="") as run:
                agent._compose(config, action="start")

            command = run.call_args.args[0]
            self.assertEqual(
                command[:8],
                [
                    "/usr/bin/docker",
                    "compose",
                    "--project-name",
                    agent.WA_FI_FENCED_WRITER_PROJECT_NAME,
                    "--profile",
                    agent.WA_FI_FENCED_WRITER_PROFILE,
                    "-f",
                    str(agent.WA_FI_FENCED_WRITER_COMPOSE_FILE),
                ],
            )
            self.assertEqual(
                command[-9:],
                [
                    "up",
                    "-d",
                    "--no-deps",
                    "--no-recreate",
                    "--no-build",
                    "--pull",
                    "never",
                    "app",
                    "bot",
                ],
            )

    def test_fenced_fi_emergency_stop_bypasses_mutable_compose_env(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self._config(directory, site="webapp_fi", mode="fenced_fi_writer")
            _write_private(config.runtime.env_file, "RELEASE_SHA=broken\n")
            with mock.patch.object(agent, "_run_runtime_command", return_value="") as run:
                agent._compose(config, action="stop")

        self.assertEqual(
            [
                call.args[0]
                for call in run.call_args_list
            ],
            [
                [
                    "/usr/bin/docker", "container", "stop", "--time", "15",
                    agent.WA_FI_FENCED_CONTAINER_NAMES["app"],
                ],
                [
                    "/usr/bin/docker", "container", "stop", "--time", "15",
                    agent.WA_FI_FENCED_CONTAINER_NAMES["bot"],
                ],
            ],
        )

    def test_fenced_fi_emergency_stop_attempts_both_and_refuses_false_success(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="fenced_fi_writer")
            with mock.patch.object(
                agent,
                "_run_runtime_command",
                side_effect=[agent.ProductionWriterLeaseAgentError("app failed"), ""],
            ) as run:
                with self.assertRaisesRegex(
                    agent.ProductionWriterLeaseAgentError, "not confirmed for: app"
                ):
                    agent._compose(config, action="stop")

        self.assertEqual(2, run.call_count)
        self.assertEqual(
            agent.WA_FI_FENCED_CONTAINER_NAMES["bot"], run.call_args_list[1].args[0][-1]
        )

    def test_fenced_fi_config_rejects_application_term_timing_not_bound_to_witness(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self._config(directory, site="webapp_fi", mode="fenced_fi_writer")
            environment = directory / "fenced-fi.env"
            _write_private(
                environment,
                environment.read_text(encoding="utf-8").replace(
                    "APPLICATION_WRITER_TERM_SAFETY_MARGIN_SECONDS=15",
                    "APPLICATION_WRITER_TERM_SAFETY_MARGIN_SECONDS=5",
                ),
            )
            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "application term timing does not match",
            ):
                agent._load_config(directory / "agent.json")

    def test_fenced_fi_config_rejects_a_lease_outside_the_mounted_term_parent(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self._config(directory, site="webapp_fi", mode="fenced_fi_writer")
            config_path = directory / "agent.json"
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["lease_file"] = str(directory / "writer-lease.json")
            _write_private(config_path, json.dumps(payload))
            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "lease file must be the writer-lease leaf",
            ):
                agent._load_config(config_path)

    def test_fenced_fi_config_requires_the_legacy_nginx_loopback_port(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self._config(directory, site="webapp_fi", mode="fenced_fi_writer")
            environment = directory / "fenced-fi.env"
            _write_private(
                environment,
                environment.read_text(encoding="utf-8").replace(
                    "WA_FI_WRITER_APP_LOCAL_PORT=8000",
                    "WA_FI_WRITER_APP_LOCAL_PORT=18001",
                ),
            )
            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "legacy Nginx upstream port 8000",
            ):
                agent._load_config(directory / "agent.json")

    def test_fenced_fi_cutover_checks_legacy_scope_before_acquiring_or_starting(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="fenced_fi_writer")
            config.lease_file.parent.mkdir(mode=0o700, exist_ok=True)
            proof = _proof(site="webapp_fi", epoch=7)
            with (
                mock.patch.object(agent, "_assert_fenced_fi_legacy_scope_is_stopped") as legacy,
                mock.patch.object(agent, "_acquire_proof", return_value=proof) as acquire,
                mock.patch.object(agent, "_start_scoped_runtime") as start,
                mock.patch.object(
                    agent,
                    "_write_fenced_fi_runtime_receipt",
                    return_value=(config.lease_file.parent / "fenced-fi-runtime-receipt.json", "d" * 64),
                ) as receipt,
            ):
                result = agent.cutover_fenced_fi_and_start(
                    config,
                    operation_id="93877f06-2671-4d78-b1f8-2c79bf759755",
                )

            self.assertEqual(result["action"], "cutover-fenced-fi")
            legacy.assert_called_once_with()
            acquire.assert_called_once()
            self.assertEqual(acquire.call_args.kwargs["purpose"], "fenced WebApp-FI controlled cutover")
            start.assert_called_once_with(config)
            receipt.assert_called_once_with(config, proof=proof)
            self.assertEqual(result["runtime_receipt_sha256"], "d" * 64)

    def test_fenced_fi_rejects_an_exited_legacy_container_with_restart_enabled(self):
        with mock.patch.object(
            agent,
            "_run_runtime_command",
            side_effect=["a" * 12, "/trading_bot_app|exited|always"],
        ):
            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "restart disabled",
            ):
                agent._assert_fenced_fi_legacy_scope_is_stopped()

    def test_fenced_fi_rejects_a_running_legacy_compose_migration_one_off(self):
        with mock.patch.object(
            agent,
            "_run_runtime_command",
            side_effect=[
                "",
                "",
                "",
                "",
                "a" * 12,
                "running|legacy_project|migration|True",
            ],
        ):
            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "one-off service is active",
            ):
                agent._assert_fenced_fi_legacy_scope_is_stopped()

    def test_fenced_fi_rejects_an_active_legacy_deployment_process(self):
        with mock.patch.object(
            agent,
            "_run_runtime_command",
            side_effect=["", "", "", "", "", " 4242 bash ./deploy.sh foreign\n"],
        ):
            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "active legacy WebApp-FI deployment process",
            ):
                agent._assert_fenced_fi_legacy_scope_is_stopped()

    def test_fenced_fi_writes_a_post_health_runtime_receipt_bound_to_the_new_term(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="fenced_fi_writer")
            config.lease_file.parent.mkdir(mode=0o700, exist_ok=True)
            app = {
                "container_id": "a" * 64,
                "container_name": agent.WA_FI_FENCED_CONTAINER_NAMES["app"],
                "image": "trading-bot:2c08",
                "image_id": "sha256:" + "a" * 64,
                "labels_sha256": "b" * 64,
                "restart_policy": "no",
            }
            bot = {
                "container_id": "c" * 64,
                "container_name": agent.WA_FI_FENCED_CONTAINER_NAMES["bot"],
                "image": "trading-bot:2c08",
                "image_id": "sha256:" + "c" * 64,
                "labels_sha256": "d" * 64,
                "restart_policy": "no",
            }
            with mock.patch.object(
                agent,
                "_capture_fenced_fi_runtime_container",
                side_effect=[app, bot],
            ):
                receipt_path, receipt_sha256 = agent._write_fenced_fi_runtime_receipt(
                    config,
                    proof=_proof(site="webapp_fi", epoch=8),
                )

            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], agent.WA_FI_FENCED_RUNTIME_RECEIPT_SCHEMA)
            self.assertEqual(payload["writer_epoch"], 8)
            self.assertEqual(payload["containers"], {"app": app, "bot": bot})
            self.assertEqual(payload["runtime_receipt_sha256"], receipt_sha256)
            self.assertEqual(oct(receipt_path.stat().st_mode & 0o777), "0o600")

    def test_fenced_fi_guard_rechecks_legacy_scope_after_renewal(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="fenced_fi_writer")
            agent._write_lease(config.lease_file, proof=_proof(site="webapp_fi", epoch=3))
            with (
                mock.patch.object(
                    agent,
                    "_assert_fenced_fi_legacy_scope_is_stopped",
                    side_effect=[None, agent.ProductionWriterLeaseAgentError("legacy restart race")],
                ) as legacy,
                mock.patch.object(
                    agent,
                    "renew_once",
                    return_value={"status": "renewed", "site": "webapp_fi"},
                ) as renew,
                mock.patch.object(agent, "_compose") as compose,
            ):
                with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "restart race"):
                    agent.guard(config, once=True)

            self.assertEqual(legacy.call_count, 2)
            renew.assert_called_once_with(config)
            compose.assert_called_once_with(config, action="stop")

    def test_fenced_fi_bot_must_be_authenticated_healthy_before_cutover_reports_success(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="fenced_fi_writer")
            with (
                mock.patch.object(agent, "_compose_capture", return_value="a" * 12),
                mock.patch.object(agent, "_run_runtime_command", return_value="exited|unhealthy"),
            ):
                with self.assertRaisesRegex(
                    agent.ProductionWriterLeaseAgentError,
                    "bot did not become authenticated and healthy",
                ):
                    agent._wait_for_fenced_fi_bot_health(config)

    def test_ir_config_requires_pinned_emergency_lease_timing(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "pinned 60/15/10"):
                self._config(
                    Path(raw),
                    site="webapp_ir",
                    lease_duration_seconds=180,
                    safety_margin_seconds=15,
                    renew_interval_seconds=30,
                )

    def test_ir_config_rejects_same_named_application_root_not_bound_by_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self._config(directory, site="webapp_ir")
            alternate = directory / "alternate-releases" / RELEASE_SHA
            alternate.mkdir(parents=True)
            alternate.chmod(0o755)
            config_path = directory / "agent.json"
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["release_provenance"]["application_release_root"] = str(alternate)
            _write_private(config_path, json.dumps(payload))

            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "does not bind this application release root",
            ):
                agent._load_config(config_path)

    def test_ir_config_requires_receipt_bound_application_image_id(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self._config(directory, site="webapp_ir")
            del self.provenance_by_receipt[config.release_provenance.receipt_path]["runtime_images"]

            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "does not bind a valid application image ID",
            ):
                agent._load_config(directory / "agent.json")

    def test_ir_compose_rechecks_mutated_application_env_before_docker(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self._config(directory, site="webapp_ir")
            alternate = directory / "alternate-releases" / RELEASE_SHA
            alternate.mkdir(parents=True)
            alternate.chmod(0o755)
            promotion_env = directory / "promotion.env"
            values = promotion_env.read_text(encoding="utf-8").replace(
                f"WA_IR_APPLICATION_RELEASE_ROOT={directory / 'releases' / RELEASE_SHA}",
                f"WA_IR_APPLICATION_RELEASE_ROOT={alternate}",
            )
            _write_private(promotion_env, values)
            with mock.patch.object(agent.subprocess, "run") as run:
                with self.assertRaisesRegex(
                    agent.ProductionWriterLeaseAgentError,
                    "does not bind the receipt application release",
                ):
                    agent._compose(config, action="start")

            run.assert_not_called()

    def test_ir_compose_rejects_configured_app_image_not_matching_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_ir")
            unexpected_image_id = "sha256:" + "d" * 64
            with mock.patch.object(
                agent,
                "_run_runtime_command",
                return_value=unexpected_image_id,
            ) as run:
                with self.assertRaisesRegex(
                    agent.ProductionWriterLeaseAgentError,
                    "configured WA-IR application image does not match",
                ):
                    agent._compose(config, action="start")

        run.assert_called_once_with(
            [
                "/usr/bin/docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                "trading-bot:2c08",
            ],
            label="configured WA-IR application image",
            timeout=30,
            capture_stdout=True,
        )

    def test_ir_existing_promoted_app_requires_receipt_image_id(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_ir")
            selection = agent.PromotionRuntimeSelection(
                db_volume="trading_bot_wa_ir_pg_snapshot-1",
                uploads_volume="trading_bot_wa_ir_uploads_snapshot-1",
                audit_volume="trading_bot_wa_ir_audit_snapshot-1",
                db_container="trading_bot_wa_ir_snapshot_db_snapshot-1",
                compose_project="trading_bot_wa_ir_snapshot_snapshot-1",
                redis_volume="trading_bot_wa_ir_redis_snapshot-1",
                release_sha=RELEASE_SHA,
            )
            containers = {"db": "a" * 12, "redis": "b" * 12, "app": "c" * 12}
            services = {container: service for service, container in containers.items()}
            volumes = {
                containers["db"]: [{"Type": "volume", "Name": selection.db_volume}],
                containers["redis"]: [{"Type": "volume", "Name": selection.redis_volume}],
                containers["app"]: [
                    {"Type": "volume", "Name": selection.uploads_volume},
                    {"Type": "volume", "Name": selection.audit_volume},
                ],
            }
            with (
                mock.patch.object(
                    agent,
                    "_compose_capture",
                    side_effect=[containers["db"], containers["redis"], containers["app"]],
                ),
                mock.patch.object(
                    agent,
                    "_inspect_labels",
                    side_effect=lambda container, **_: {
                        "com.docker.compose.project": agent.WA_IR_PROMOTION_PROJECT_NAME,
                        "com.docker.compose.service": services[container],
                    },
                ),
                mock.patch.object(
                    agent,
                    "_inspect_mounts",
                    side_effect=lambda container, **_: volumes[container],
                ),
                mock.patch.object(
                    agent,
                    "_run_runtime_command",
                    return_value="sha256:" + "d" * 64,
                ) as run,
            ):
                with self.assertRaisesRegex(
                    agent.ProductionWriterLeaseAgentError,
                    "promoted application image does not match",
                ):
                    agent._assert_existing_promoted_runtime_matches_selection(
                        config,
                        selection=selection,
                    )

        run.assert_called_once_with(
            ["/usr/bin/docker", "inspect", "--format", "{{.Image}}", containers["app"]],
            label="promoted app image id",
            timeout=30,
            capture_stdout=True,
        )

    def test_generic_fi_bootstrap_cannot_reactivate_legacy_sync_worker(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            with mock.patch.object(agent, "_run_runtime_command") as runtime:
                with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "generic WebApp-FI writer mode is retired"):
                    self._config(directory, site="webapp_fi", mode="writer")

            runtime.assert_not_called()

    def test_guard_keeps_scoped_services_running_for_transient_renew_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="fenced_fi_writer")
            agent._write_lease(config.lease_file, proof=_proof(site="webapp_fi", epoch=3))
            with (
                mock.patch.object(agent, "_assert_fenced_fi_legacy_scope_is_stopped"),
                mock.patch.object(
                    agent,
                    "renew_once",
                    side_effect=agent.WriterWitnessUnavailable("Witness unavailable"),
                ),
                mock.patch.object(agent, "_compose") as compose,
            ):
                result = agent.guard(config, once=True)

            self.assertEqual(result["status"], "renewal_degraded")
            compose.assert_not_called()

    def test_promotion_recovery_renews_own_live_term_after_ambiguous_acquire(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_ir")
            now = datetime.now(timezone.utc)
            status = {
                "state": {
                    "holder_site": "webapp_ir",
                    "writer_epoch": 7,
                    "lease_id": "lease-7",
                    "lease_status": "leased",
                    "issued_at": (now - timedelta(seconds=10)).isoformat(),
                    "expires_at": (now + timedelta(seconds=90)).isoformat(),
                    "transition_id": "transition-7",
                }
            }
            proof = _proof(site="webapp_ir", epoch=7)
            proof["lease_id"] = "lease-7"
            with (
                mock.patch.object(agent, "_status", return_value=status),
                mock.patch.object(agent, "_transition", return_value={"proof": proof}) as transition,
                mock.patch.object(agent, "_validate_proof", return_value=proof),
            ):
                recovered = agent._acquire_proof(
                    config,
                    operation_id="e5dba03d-ae0e-41a1-97f9-053fd5e9cf11",
                    purpose="IR promotion",
                    allow_live_local_recovery=True,
                )

            self.assertEqual(recovered["writer_epoch"], 7)
            self.assertEqual(transition.call_args.kwargs["action"], "renew")
            self.assertEqual(load_production_writer_lease(config.lease_file).lease_id, "lease-7")

    def test_signed_witness_proof_is_verified_before_local_lease_write(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_ir")
            private_key = Ed25519PrivateKey.generate()
            public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")
            now = datetime.now(timezone.utc)
            unsigned = {
                "version": 1,
                "authority": "webapp",
                "holder_site": "webapp_ir",
                "writer_epoch": 4,
                "lease_id": "lease-4",
                "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=60)).isoformat(),
                "witness_transition_id": "transition-4",
            }
            signature = private_key.sign(canonical_json_bytes(unsigned))
            proof = {**unsigned, "signature": base64.b64encode(signature).decode("ascii")}

            validated = agent._validate_proof(
                proof,
                config=replace(config.witness, public_key=public_key),
                expected_epoch=4,
            )

            self.assertEqual(validated["lease_id"], "lease-4")

    def test_guard_stops_only_scoped_runtime_when_lease_is_unsafe(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="fenced_fi_writer")
            now = datetime.now(timezone.utc)
            stale = _proof(site="webapp_fi", epoch=3)
            stale["issued_at"] = (now - timedelta(seconds=60)).isoformat()
            stale["expires_at"] = (now + timedelta(seconds=5)).isoformat()
            agent._write_lease(config.lease_file, proof=stale)
            with mock.patch.object(agent, "_assert_fenced_fi_legacy_scope_is_stopped"), mock.patch.object(agent, "_compose") as compose:
                with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "unsafe"):
                    agent.guard(config, once=True)

            compose.assert_called_once_with(config, action="stop")

    def test_bot_fi_observer_never_renews_and_requires_active_fi_term(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="observer")
            proof = _proof(site="webapp_fi", epoch=3)
            status = {
                "state": {
                    "holder_site": "webapp_fi",
                    "writer_epoch": 3,
                    "lease_id": "lease-3",
                    "lease_status": "leased",
                    "issued_at": proof["issued_at"],
                    "expires_at": proof["expires_at"],
                    "transition_id": "transition-3",
                }
            }
            with (
                mock.patch.object(agent, "_status", return_value=status),
                mock.patch.object(agent, "renew_once") as renew,
                mock.patch.object(agent, "_compose") as compose,
            ):
                result = agent.guard(config, once=True)

            self.assertEqual(result["status"], "observed")
            self.assertEqual(result["writer_epoch"], 3)
            renew.assert_not_called()
            compose.assert_not_called()
            self.assertEqual(config.runtime.services, ("bot", "sync_worker"))
            self.assertIsNone(config.lease_file)

    def test_bot_fi_observer_stops_when_witness_is_not_active_fi(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="observer")
            proof = _proof(site="webapp_fi", epoch=3)
            status = {
                "state": {
                    "holder_site": "webapp_ir",
                    "writer_epoch": 4,
                    "lease_id": "lease-4",
                    "lease_status": "leased",
                    "issued_at": proof["issued_at"],
                    "expires_at": proof["expires_at"],
                    "transition_id": "transition-4",
                }
            }
            with (
                mock.patch.object(agent, "_status", return_value=status),
                mock.patch.object(agent, "_compose") as compose,
            ):
                with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "not active"):
                    agent.guard(config, once=True)

            compose.assert_called_once_with(config, action="stop")

    def test_bot_fi_observer_stops_immediately_when_witness_is_unavailable(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="observer")
            with (
                mock.patch.object(
                    agent,
                    "_status",
                    side_effect=agent.WriterWitnessUnavailable("Witness unavailable"),
                ),
                mock.patch.object(agent, "_compose") as compose,
            ):
                with self.assertRaisesRegex(agent.WriterWitnessUnavailable, "unavailable"):
                    agent.guard(config, once=True)

            compose.assert_called_once_with(config, action="stop")

    def test_observer_rejects_a_local_lease_or_ir_site(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            secret = directory / "witness.secret"
            public_key = directory / "witness.pub"
            _write_private(secret, "s" * 32)
            _write_private(public_key, base64.b64encode(b"p" * 32).decode("ascii"))
            payload = {
                "schema": agent.AGENT_SCHEMA,
                "mode": "observer",
                "site": "webapp_fi",
                "lease_file": str(directory / "writer-lease.json"),
                "runtime": {
                    "compose_file": str(directory / "isolated-compose.yml"),
                    "env_file": None,
                    "selection_env_file": None,
                    "services": ["bot", "sync_worker"],
                },
                "witness": {
                    "url": "https://witness.example.test",
                    "key_id": "host-key-1",
                    "secret_file": str(secret),
                    "public_key_file": str(public_key),
                    "ca_bundle": None,
                    "timeout_seconds": 2,
                    "lease_duration_seconds": 180,
                    "safety_margin_seconds": 15,
                    "renew_interval_seconds": 30,
                },
            }
            config_path = directory / "agent.json"
            _write_private(config_path, json.dumps(payload))
            with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "must not configure"):
                agent._load_config(config_path)

            payload["lease_file"] = None
            payload["site"] = "webapp_ir"
            _write_private(config_path, json.dumps(payload))
            with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "only for Bot-FI"):
                agent._load_config(config_path)

    def test_bot_fi_observer_compose_scope_never_includes_database(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi", mode="observer")
            completed = mock.Mock(returncode=0)
            with mock.patch.object(agent.subprocess, "run", return_value=completed) as run:
                agent._compose(config, action="stop")

            command = run.call_args.args[0]
            self.assertEqual(command[-5:], ["stop", "--timeout", "15", "bot", "sync_worker"])
            self.assertNotIn("app", command)
            self.assertNotIn("db", command)

    def test_ir_promotion_requires_fresh_receipt_and_writes_routing_proof(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self._config(directory, site="webapp_ir")
            receipt_path = directory / "restore-receipt.json"
            proof_path = directory / "promotion-proof.json"
            active_snapshot_path = directory / "active-snapshot.json"
            receipt = _restore_receipt(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                published_at=datetime.now(timezone.utc) - timedelta(seconds=4),
            )
            _write_private(receipt_path, json.dumps(receipt))
            _write_private(
                active_snapshot_path,
                json.dumps(_active_snapshot(receipt=receipt, receipt_path=receipt_path)),
            )
            witness_proof = _proof(site="webapp_ir", epoch=9)
            with (
                mock.patch.object(agent, "_acquire_proof", return_value=witness_proof) as acquire,
                mock.patch.object(agent, "_assert_existing_promoted_runtime_matches_selection") as existing,
                mock.patch.object(agent, "_stop_selected_snapshot_db", return_value=True) as stop_snapshot,
                mock.patch.object(agent, "_start_scoped_runtime") as start,
                mock.patch.object(agent, "_renew_activation_proof", return_value=witness_proof) as renew,
                mock.patch.object(
                    agent,
                    "_write_promoted_runtime_binding",
                    return_value="f" * 64,
                ) as write_binding,
            ):
                result = agent.activate_from_snapshot(
                    config,
                    action="promote_ir",
                    operation_id="93877f06-2671-4d78-b1f8-2c79bf759755",
                    restore_receipt=receipt_path,
                    active_snapshot=active_snapshot_path,
                    proof_output=proof_path,
                )

            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            self.assertEqual(result["action"], "promote_ir")
            self.assertEqual(proof["target_site"], "webapp_ir")
            self.assertEqual(proof["epoch"], 9)
            acquire.assert_called_once()
            self.assertFalse(acquire.call_args.kwargs["persist_lease"])
            existing.assert_called_once()
            stop_snapshot.assert_called_once()
            start.assert_called_once_with(config)
            renew.assert_called_once()
            write_binding.assert_called_once()
            self.assertEqual(result["runtime_binding_sha256"], "f" * 64)
            self.assertEqual(os.stat(proof_path).st_mode & 0o777, 0o600)
            self.assertEqual(
                (
                    "WA_IR_CANDIDATE_AUDIT_VOLUME=trading_bot_wa_ir_audit_snapshot-1\n"
                    "WA_IR_CANDIDATE_DB_VOLUME=trading_bot_wa_ir_pg_snapshot-1\n"
                    "WA_IR_CANDIDATE_UPLOADS_VOLUME=trading_bot_wa_ir_uploads_snapshot-1\n"
                    "WA_IR_REDIS_VOLUME_NAME=trading_bot_wa_ir_redis_snapshot-1\n"
                ),
                config.runtime.selection_env_file.read_text(encoding="ascii"),
            )

    def test_ir_promotion_rejects_stale_receipt_before_witness_transition(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self._config(directory, site="webapp_ir")
            receipt_path = directory / "restore-receipt.json"
            active_snapshot_path = directory / "active-snapshot.json"
            _write_private(
                receipt_path,
                json.dumps(
                    _restore_receipt(
                        source_site="webapp_fi",
                        destination_site="webapp_ir",
                        published_at=datetime.now(timezone.utc) - timedelta(seconds=151),
                    )
                ),
            )
            _write_private(active_snapshot_path, "{}")
            with mock.patch.object(agent, "_acquire_proof") as acquire:
                with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "cannot support"):
                    agent.activate_from_snapshot(
                        config,
                        action="promote_ir",
                        operation_id="8adcb08f-7fa2-467c-ae59-e5379745a2e7",
                        restore_receipt=receipt_path,
                        active_snapshot=active_snapshot_path,
                        proof_output=directory / "promotion-proof.json",
                    )

            acquire.assert_not_called()

    def test_ir_promotion_rejects_an_unbound_active_snapshot_before_witness_transition(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self._config(directory, site="webapp_ir")
            receipt_path = directory / "restore-receipt.json"
            active_snapshot_path = directory / "active-snapshot.json"
            receipt = _restore_receipt(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                published_at=datetime.now(timezone.utc) - timedelta(seconds=4),
            )
            _write_private(receipt_path, json.dumps(receipt))
            pointer = _active_snapshot(receipt=receipt, receipt_path=receipt_path)
            pointer["witness_restore_receipt"]["receipt_sha256"] = "0" * 64
            _write_private(active_snapshot_path, json.dumps(pointer))
            with mock.patch.object(agent, "_acquire_proof") as acquire:
                with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "does not bind"):
                    agent.activate_from_snapshot(
                        config,
                        action="promote_ir",
                        operation_id="3ee0a982-3595-4a3d-b2a0-5963d3c7ef03",
                        restore_receipt=receipt_path,
                        active_snapshot=active_snapshot_path,
                        proof_output=directory / "promotion-proof.json",
                    )

            acquire.assert_not_called()

    def test_ir_promotion_watch_uses_deterministic_receipt_bound_operation(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self._config(directory, site="webapp_ir")
            receipt_path = directory / "restore-receipt.json"
            active_snapshot_path = directory / "active-snapshot.json"
            payload = _restore_receipt(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                published_at=datetime.now(timezone.utc) - timedelta(seconds=4),
            )
            _write_private(receipt_path, json.dumps(payload))
            _write_private(
                active_snapshot_path,
                json.dumps(_active_snapshot(receipt=payload, receipt_path=receipt_path)),
            )
            expected_operation = agent._automatic_operation_id(
                action="promote_ir", receipt_sha256=payload["receipt_sha256"]
            )
            with mock.patch.object(
                agent,
                "activate_from_snapshot",
                return_value={"status": "activated", "action": "promote_ir"},
            ) as activate:
                result = agent.promote_watch(
                    config,
                    restore_receipt=receipt_path,
                    active_snapshot=active_snapshot_path,
                    proof_directory=directory / "promotion-proofs",
                    poll_seconds=2,
                    once=True,
                )

            self.assertEqual(result["status"], "activated")
            self.assertEqual(activate.call_args.kwargs["operation_id"], expected_operation)
            self.assertEqual(
                activate.call_args.kwargs["expected_receipt_sha256"], payload["receipt_sha256"]
            )
            self.assertEqual(activate.call_args.kwargs["active_snapshot"], active_snapshot_path)

    def test_existing_proof_requires_bound_live_selected_and_healthy_runtime(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, receipt_path, active_snapshot_path, proof_directory, _receipt = (
                self._existing_activation_fixture(directory)
            )
            runtime = {"db": "a" * 12, "redis": "b" * 12, "app": "c" * 12}
            with (
                mock.patch.object(
                    agent,
                    "_assert_existing_promoted_runtime_matches_selection",
                    return_value=runtime,
                ) as selected,
                mock.patch.object(agent, "_wait_for_ir_app_health") as health,
                mock.patch.object(agent, "activate_from_snapshot") as activate,
            ):
                result = agent.promote_watch(
                    config,
                    restore_receipt=receipt_path,
                    active_snapshot=active_snapshot_path,
                    proof_directory=proof_directory,
                    poll_seconds=2,
                    once=True,
                )

        self.assertEqual(result["status"], "already_activated")
        self.assertEqual(result["writer_epoch"], 9)
        self.assertEqual(selected.call_count, 2)
        health.assert_called_once_with(config)
        activate.assert_not_called()

    def test_existing_proof_refuses_mismatched_live_local_term_before_runtime_access(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, receipt_path, active_snapshot_path, proof_directory, _receipt = (
                self._existing_activation_fixture(directory)
            )
            agent._write_lease(config.lease_file, proof=_proof(site="webapp_ir", epoch=10))
            with (
                mock.patch.object(agent, "_assert_existing_promoted_runtime_matches_selection") as selected,
                mock.patch.object(agent, "_wait_for_ir_app_health") as health,
            ):
                with self.assertRaisesRegex(
                    agent.ProductionWriterLeaseAgentError,
                    "does not match existing promotion proof",
                ):
                    agent.promote_watch(
                        config,
                        restore_receipt=receipt_path,
                        active_snapshot=active_snapshot_path,
                        proof_directory=proof_directory,
                        poll_seconds=2,
                        once=True,
                    )

        selected.assert_not_called()
        health.assert_not_called()

    def test_existing_proof_refuses_unbound_active_snapshot_before_runtime_access(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, receipt_path, active_snapshot_path, proof_directory, receipt = (
                self._existing_activation_fixture(directory)
            )
            pointer = _active_snapshot(receipt=receipt, receipt_path=receipt_path)
            pointer["witness_restore_receipt"]["receipt_sha256"] = "0" * 64
            _write_private(active_snapshot_path, json.dumps(pointer))
            with (
                mock.patch.object(agent, "_assert_existing_promoted_runtime_matches_selection") as selected,
                mock.patch.object(agent, "_wait_for_ir_app_health") as health,
            ):
                with self.assertRaisesRegex(
                    agent.ProductionWriterLeaseAgentError,
                    "does not bind this fresh Witness receipt",
                ):
                    agent.promote_watch(
                        config,
                        restore_receipt=receipt_path,
                        active_snapshot=active_snapshot_path,
                        proof_directory=proof_directory,
                        poll_seconds=2,
                        once=True,
                    )

        selected.assert_not_called()
        health.assert_not_called()

    def test_existing_proof_refuses_absent_or_unhealthy_runtime(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, receipt_path, active_snapshot_path, proof_directory, _receipt = (
                self._existing_activation_fixture(directory)
            )
            with (
                mock.patch.object(
                    agent,
                    "_assert_existing_promoted_runtime_matches_selection",
                    return_value=None,
                ) as selected,
                mock.patch.object(agent, "_wait_for_ir_app_health") as health,
            ):
                with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "runtime is absent"):
                    agent.promote_watch(
                        config,
                        restore_receipt=receipt_path,
                        active_snapshot=active_snapshot_path,
                        proof_directory=proof_directory,
                        poll_seconds=2,
                        once=True,
                    )

            health.assert_not_called()
            self.assertEqual(selected.call_count, 1)

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, receipt_path, active_snapshot_path, proof_directory, _receipt = (
                self._existing_activation_fixture(directory)
            )
            runtime = {"db": "a" * 12, "redis": "b" * 12, "app": "c" * 12}
            with (
                mock.patch.object(
                    agent,
                    "_assert_existing_promoted_runtime_matches_selection",
                    return_value=runtime,
                ) as selected,
                mock.patch.object(
                    agent,
                    "_wait_for_ir_app_health",
                    side_effect=agent.ProductionWriterLeaseAgentError(
                        "promoted app became unhealthy before routing"
                    ),
                ) as health,
            ):
                with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "unhealthy"):
                    agent.promote_watch(
                        config,
                        restore_receipt=receipt_path,
                        active_snapshot=active_snapshot_path,
                        proof_directory=proof_directory,
                        poll_seconds=2,
                        once=True,
                    )

        self.assertEqual(selected.call_count, 1)
        health.assert_called_once_with(config)

    def test_explicit_promoted_runtime_recovery_starts_only_persisted_container_ids(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, receipt_path, active_snapshot_path, proof_path, _receipt = self._recovery_fixture(directory)
            stopped = {"db": "exited", "redis": "exited", "app": "exited"}
            running = {"db": "running", "redis": "running", "app": "running"}
            with (
                mock.patch.object(
                    agent,
                    "_inspect_bound_promoted_runtime",
                    side_effect=[stopped, running],
                ),
                mock.patch.object(agent, "_start_bound_promoted_container") as start,
                mock.patch.object(agent, "_wait_for_promoted_container_health") as health,
                mock.patch.object(agent, "_wait_for_promoted_redis_running") as redis,
                mock.patch.object(agent, "_promoted_container_health", return_value="running|healthy"),
                mock.patch.object(agent, "_best_effort_stop_bound_promoted_runtime") as fence,
                mock.patch.object(agent, "_compose") as compose,
            ):
                result = agent.recover_promoted_runtime(
                    config,
                    restore_receipt=receipt_path,
                    active_snapshot=active_snapshot_path,
                    promotion_proof=proof_path,
                )

        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["action"], "recover-promoted-runtime")
        self.assertEqual(
            [call.kwargs["service"] for call in start.call_args_list],
            ["db", "redis", "app"],
        )
        self.assertEqual(
            [call.args[0].container_id for call in start.call_args_list],
            ["a" * 64, "b" * 64, "c" * 64],
        )
        self.assertEqual([call.kwargs["service"] for call in health.call_args_list], ["db", "app"])
        redis.assert_called_once()
        fence.assert_not_called()
        compose.assert_not_called()

    def test_promoted_runtime_recovery_binding_requires_receipt_app_image_id(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, receipt_path, active_snapshot_path, proof_path, receipt = self._recovery_fixture(directory)
            binding_path = agent._runtime_binding_path(proof_path)
            binding = json.loads(binding_path.read_text(encoding="utf-8"))
            binding["containers"]["app"]["image_id"] = "sha256:" + "d" * 64
            binding["binding_sha256"] = agent._runtime_binding_hash(
                {key: value for key, value in binding.items() if key != "binding_sha256"}
            )
            _write_private(binding_path, json.dumps(binding))
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            snapshot = parse_restore_receipt(receipt, action="promote_ir")
            selection = agent._load_promotion_runtime_selection(
                active_snapshot_path,
                restore_receipt_path=receipt_path,
                restore_receipt=receipt,
                snapshot=snapshot,
            )

            with self.assertRaisesRegex(
                agent.ProductionWriterLeaseAgentError,
                "recovery binding container is unsafe",
            ):
                agent._load_promoted_runtime_binding(
                    proof_path,
                    proof=proof,
                    snapshot=snapshot,
                    selection=selection,
                    config=config,
                )

    def test_promoted_runtime_recovery_refuses_missing_binding_before_any_start(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, receipt_path, active_snapshot_path, proof_directory, receipt = (
                self._existing_activation_fixture(directory)
            )
            snapshot = parse_restore_receipt(receipt, action="promote_ir")
            proof_path = agent._automatic_proof_path(
                directory=proof_directory,
                action="promote_ir",
                snapshot_id=snapshot.snapshot_id,
                receipt_sha256=snapshot.receipt_sha256,
            )
            with mock.patch.object(agent, "_start_bound_promoted_container") as start:
                with self.assertRaisesRegex(
                    agent.ProductionWriterLeaseAgentError,
                    "cannot securely open promoted runtime recovery binding",
                ):
                    agent.recover_promoted_runtime(
                        config,
                        restore_receipt=receipt_path,
                        active_snapshot=active_snapshot_path,
                        promotion_proof=proof_path,
                    )

        start.assert_not_called()

    def test_promoted_runtime_recovery_refuses_to_restart_a_running_app(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, receipt_path, active_snapshot_path, proof_path, _receipt = self._recovery_fixture(directory)
            with (
                mock.patch.object(
                    agent,
                    "_inspect_bound_promoted_runtime",
                    return_value={"db": "running", "redis": "running", "app": "running"},
                ),
                mock.patch.object(agent, "_start_bound_promoted_container") as start,
            ):
                with self.assertRaisesRegex(
                    agent.ProductionWriterLeaseAgentError,
                    "refuses to restart a running container",
                ):
                    agent.recover_promoted_runtime(
                        config,
                        restore_receipt=receipt_path,
                        active_snapshot=active_snapshot_path,
                        promotion_proof=proof_path,
                    )

        start.assert_not_called()

    def test_promoted_runtime_recovery_fences_exact_bound_ids_when_final_lease_recheck_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config, receipt_path, active_snapshot_path, proof_path, _receipt = self._recovery_fixture(directory)
            lease = load_production_writer_lease(config.lease_file)
            stopped = {"db": "exited", "redis": "exited", "app": "exited"}
            running = {"db": "running", "redis": "running", "app": "running"}
            with (
                mock.patch.object(
                    agent,
                    "_inspect_bound_promoted_runtime",
                    side_effect=[stopped, running],
                ),
                mock.patch.object(agent, "_start_bound_promoted_container"),
                mock.patch.object(agent, "_wait_for_promoted_container_health"),
                mock.patch.object(agent, "_wait_for_promoted_redis_running"),
                mock.patch.object(agent, "_promoted_container_health", return_value="running|healthy"),
                mock.patch.object(
                    agent,
                    "_assert_live_matching_promotion_lease",
                    side_effect=[lease, agent.ProductionWriterLeaseAgentError("lease changed")],
                ) as lease_check,
                mock.patch.object(agent, "_best_effort_stop_bound_promoted_runtime") as fence,
                mock.patch.object(agent, "_compose") as compose,
            ):
                with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "lease changed"):
                    agent.recover_promoted_runtime(
                        config,
                        restore_receipt=receipt_path,
                        active_snapshot=active_snapshot_path,
                        promotion_proof=proof_path,
                    )

        self.assertEqual(lease_check.call_count, 2)
        fence.assert_called_once()
        bound_ids = [
            fence.call_args.args[0][service].container_id
            for service in ("db", "redis", "app")
        ]
        self.assertEqual(bound_ids, ["a" * 64, "b" * 64, "c" * 64])
        compose.assert_not_called()

    def test_recovery_cli_is_explicit_and_requires_all_three_bindings(self):
        args = agent.build_parser().parse_args(
            [
                "--config",
                "/etc/trading-bot-three-site/writer.json",
                "recover-promoted-runtime",
                "--restore-receipt",
                "/var/lib/trading-bot-three-site/restore-receipt.json",
                "--active-snapshot",
                "/var/lib/trading-bot-three-site/active-snapshot.json",
                "--promotion-proof",
                "/var/lib/trading-bot-three-site/proofs/promote.json",
            ]
        )
        self.assertEqual(args.action, "recover-promoted-runtime")
        self.assertEqual(args.promotion_proof.name, "promote.json")


if __name__ == "__main__":
    unittest.main()
