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
from core.production_snapshot_promotion import canonical_json_bytes
from core.production_writer_lease import load_production_writer_lease
from scripts import production_writer_lease_agent as agent


RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"


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


class ProductionWriterLeaseAgentTests(unittest.TestCase):
    def _config(
        self,
        directory: Path,
        *,
        site: str,
        mode: str = "writer",
        services: list[str] | None = None,
    ) -> agent.AgentConfig:
        secret = directory / "witness.secret"
        public_key = directory / "witness.pub"
        config_path = directory / "agent.json"
        _write_private(secret, "s" * 32)
        _write_private(public_key, base64.b64encode(b"p" * 32).decode("ascii"))
        config = {
            "schema": agent.AGENT_SCHEMA,
            "mode": mode,
            "site": site,
            "lease_file": str(directory / "writer-lease.json") if mode == "writer" else None,
            "runtime": {
                "compose_file": str(directory / "isolated-compose.yml"),
                "env_file": None,
                "services": services if services is not None else (
                    ["app", "sync_worker"] if mode == "writer" else ["bot", "sync_worker"]
                ),
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
        _write_private(config_path, json.dumps(config))
        return agent._load_config(config_path)

    def test_config_requires_exactly_app_and_sync_worker(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "unsupported|writer mode"):
                self._config(Path(raw), site="webapp_fi", services=["app"])

    def test_bootstrap_writes_lease_and_starts_only_scoped_services(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self._config(directory, site="webapp_fi")
            status = {
                "state": {
                    "holder_site": None,
                    "writer_epoch": 0,
                    "lease_id": None,
                    "lease_status": "vacant",
                    "issued_at": None,
                    "expires_at": None,
                    "transition_id": "transition-bootstrap",
                }
            }
            proof = _proof(site="webapp_fi", epoch=1)
            completed = mock.Mock(returncode=0)
            with (
                mock.patch.object(agent, "_status", return_value=status),
                mock.patch.object(agent, "_transition", return_value={"proof": proof}),
                mock.patch.object(agent, "_validate_proof", return_value=proof),
                mock.patch.object(agent.subprocess, "run", return_value=completed) as run,
            ):
                result = agent.bootstrap_fi_and_start(
                    config, operation_id="8d7fa0a2-1b5f-4eb8-94fd-4ccba61f422e"
                )

            command = run.call_args.args[0]
            self.assertEqual(
                command[-6:],
                ["up", "-d", "--no-deps", "--no-recreate", "app", "sync_worker"],
            )
            self.assertNotIn("db", command)
            self.assertEqual(result["writer_epoch"], 1)
            self.assertEqual(load_production_writer_lease(config.lease_file).holder_site, "webapp_fi")

    def test_guard_keeps_scoped_services_running_for_transient_renew_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), site="webapp_fi")
            agent._write_lease(config.lease_file, proof=_proof(site="webapp_fi", epoch=3))
            with (
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
                "expires_at": (now + timedelta(seconds=180)).isoformat(),
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
            config = self._config(Path(raw), site="webapp_fi")
            now = datetime.now(timezone.utc)
            stale = _proof(site="webapp_fi", epoch=3)
            stale["issued_at"] = (now - timedelta(seconds=60)).isoformat()
            stale["expires_at"] = (now + timedelta(seconds=5)).isoformat()
            agent._write_lease(config.lease_file, proof=stale)
            with mock.patch.object(agent, "_compose") as compose:
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
            _write_private(
                receipt_path,
                json.dumps(
                    _restore_receipt(
                        source_site="webapp_fi",
                        destination_site="webapp_ir",
                        published_at=datetime.now(timezone.utc) - timedelta(seconds=4),
                    )
                ),
            )
            witness_proof = _proof(site="webapp_ir", epoch=9)
            with (
                mock.patch.object(agent, "_acquire_proof", return_value=witness_proof) as acquire,
                mock.patch.object(agent, "_start_scoped_runtime") as start,
            ):
                result = agent.activate_from_snapshot(
                    config,
                    action="promote_ir",
                    operation_id="93877f06-2671-4d78-b1f8-2c79bf759755",
                    restore_receipt=receipt_path,
                    proof_output=proof_path,
                )

            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            self.assertEqual(result["action"], "promote_ir")
            self.assertEqual(proof["target_site"], "webapp_ir")
            self.assertEqual(proof["epoch"], 9)
            acquire.assert_called_once()
            start.assert_called_once_with(config)
            self.assertEqual(os.stat(proof_path).st_mode & 0o777, 0o600)

    def test_ir_promotion_rejects_stale_receipt_before_witness_transition(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self._config(directory, site="webapp_ir")
            receipt_path = directory / "restore-receipt.json"
            _write_private(
                receipt_path,
                json.dumps(
                    _restore_receipt(
                        source_site="webapp_fi",
                        destination_site="webapp_ir",
                        published_at=datetime.now(timezone.utc) - timedelta(seconds=31),
                    )
                ),
            )
            with mock.patch.object(agent, "_acquire_proof") as acquire:
                with self.assertRaisesRegex(agent.ProductionWriterLeaseAgentError, "cannot support"):
                    agent.activate_from_snapshot(
                        config,
                        action="promote_ir",
                        operation_id="8adcb08f-7fa2-467c-ae59-e5379745a2e7",
                        restore_receipt=receipt_path,
                        proof_output=directory / "promotion-proof.json",
                    )

            acquire.assert_not_called()

    def test_ir_promotion_watch_uses_deterministic_receipt_bound_operation(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            config = self._config(directory, site="webapp_ir")
            receipt_path = directory / "restore-receipt.json"
            payload = _restore_receipt(
                source_site="webapp_fi",
                destination_site="webapp_ir",
                published_at=datetime.now(timezone.utc) - timedelta(seconds=4),
            )
            _write_private(receipt_path, json.dumps(payload))
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
                    proof_directory=directory / "promotion-proofs",
                    poll_seconds=2,
                    once=True,
                )

            self.assertEqual(result["status"], "activated")
            self.assertEqual(activate.call_args.kwargs["operation_id"], expected_operation)
            self.assertEqual(
                activate.call_args.kwargs["expected_receipt_sha256"], payload["receipt_sha256"]
            )


if __name__ == "__main__":
    unittest.main()
