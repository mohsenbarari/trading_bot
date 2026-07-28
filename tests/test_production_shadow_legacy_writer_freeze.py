from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.secure_file_io import write_secure_new_bytes
from scripts import produce_production_shadow_source_snapshot as SOURCE
from scripts import production_shadow_legacy_writer_freeze as MODULE


OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40
LEGACY_RELEASE_SHA = "c" * 40
NGINX_MANIFEST_SHA256 = "d" * 64
NGINX_AGGREGATE_SHA256 = "a" * 64
COORDINATED_RECEIPT_SHA256 = "b" * 64
LIVE_LEASE_CLAIM_SHA256 = "c" * 64
GLOBAL_FREEZE_SHA256 = "1" * 64


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


class BindingFixture:
    def __init__(self, root: Path, *, role: str = "bot_fi") -> None:
        self.root = root
        self.role = role
        self.project = SOURCE.SOURCE_PROJECTS[role]
        self.path = root / "binding.json"
        self.document = {
            "schema": SOURCE.BINDING_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "legacy_release_sha": LEGACY_RELEASE_SHA,
            "role": role,
            "source_project": self.project,
            "containers": dict(SOURCE.SOURCE_CONTAINERS),
            "images": {
                **SOURCE.SOURCE_IMAGE_REFERENCES[role],
                "restore_postgres": (
                    "trading_bot_postgres_boottime:15-" + RELEASE_SHA
                ),
            },
            "volumes": {
                kind: f"{self.project}_{suffix}"
                for kind, suffix in SOURCE.VOLUME_SUFFIXES.items()
            },
            "controller_manifest_sha256": "1" * 64,
            "approval_sha256": "2" * 64,
            "mode": "frozen-final",
        }
        self.path.write_bytes(canonical(self.document))
        self.path.chmod(0o600)

    def binding(self) -> SOURCE.SnapshotBinding:
        return SOURCE.load_binding(self.path)


class FakeRuntime:
    def __init__(self, binding: SOURCE.SnapshotBinding) -> None:
        self.binding = binding
        characters = {
            "application": "3",
            "bot": "4",
            "sync_worker": "5",
        }
        self.writers = {
            kind: {
                "id": characters[kind] * 64,
                "name": name,
                "service": service,
                "image_id": "sha256:" + "6" * 64,
                "release_sha": binding.legacy_release_sha,
            }
            for kind, name, service in MODULE.ROLE_WRITERS[binding.role]
        }
        self.running = {kind: True for kind in self.writers}
        self.started_at = {
            kind: f"2026-07-27T00:00:0{index}Z"
            for index, kind in enumerate(self.writers, start=1)
        }
        self.restart_count = {kind: 0 for kind in self.writers}
        self.application_ready = True
        self.calls: list[tuple[str, ...]] = []
        self.inventory = SOURCE.SourceInventory(
            containers={
                "database": {
                    "id": "7" * 64,
                    "running": True,
                },
                "application": {
                    "id": self.writers["application"]["id"],
                    "running": True,
                },
                "redis": {
                    "id": "8" * 64,
                    "running": True,
                },
            },
            images={
                "application": SOURCE.ImageIdentity(
                    binding.images["application"],
                    "sha256:" + "6" * 64,
                    {},
                )
            },
            volumes={
                "uploads": {"mountpoint": "/volumes/uploads"},
                "audit": {"mountpoint": "/volumes/audit"},
            },
            canonical_sha256="9" * 64,
        )

    def refresh(self, _binding):
        self.inventory.containers["application"]["running"] = self.running[
            "application"
        ]
        return (
            self.inventory,
            {key: dict(value) for key, value in self.writers.items()},
            dict(self.running),
        )

    def inspect(self, kind: str, identity: str):
        if kind != "container":
            raise AssertionError((kind, identity))
        for writer_kind, row in self.writers.items():
            if identity in {row["id"], row["name"]}:
                return {
                    "Id": row["id"],
                    "Name": "/" + row["name"],
                    "State": {
                        "Running": self.running[writer_kind],
                        "Paused": False,
                        "Restarting": False,
                        "Dead": False,
                        "StartedAt": self.started_at[writer_kind],
                        "Pid": 1000 + list(self.writers).index(writer_kind),
                    },
                    "RestartCount": self.restart_count[writer_kind],
                    "Image": row["image_id"],
                    "Config": {
                        "Image": self.binding.images["application"],
                        "Labels": {
                            "com.docker.compose.project": (
                                self.binding.source_project
                            ),
                            "com.docker.compose.service": row["service"],
                            "com.docker.compose.oneoff": "False",
                        },
                        "Env": [
                            f"RELEASE_SHA={self.binding.legacy_release_sha}",
                            "TRADING_BOT_SERVICE="
                            + MODULE.ROLE_SERVICE_ENV[writer_kind],
                        ],
                    },
                }
        raise AssertionError((kind, identity))

    def runner(self, arguments, timeout: int) -> str:
        del timeout
        argv = tuple(arguments)
        self.calls.append(argv)
        if argv and argv[0] == MODULE.CURL:
            if not self.application_ready:
                raise MODULE.LegacyWriterFreezeError("application not ready")
            return "200"
        if len(argv) >= 3 and argv[0] == MODULE.DOCKER:
            action = argv[1]
            identifier = argv[2] if action == "top" else argv[-1]
            for kind, row in self.writers.items():
                if identifier == row["id"]:
                    if action == "stop":
                        self.running[kind] = False
                        return row["name"]
                    if action == "start":
                        self.running[kind] = True
                        return row["name"]
                    if action == "top" and self.running[kind]:
                        pid = 1000 + list(self.writers).index(kind)
                        return f"{pid} python"
        raise AssertionError(argv)


class LegacyWriterFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = BindingFixture(self.root)
        self.binding = self.fixture.binding()
        self.secret_root = self.root / "secrets"
        operation_root = self.secret_root / OPERATION_ID
        operation_root.mkdir(parents=True, mode=0o700)
        operation_root.chmod(0o700)
        self.nginx_manifest = self.root / "nginx-manifest.json"
        self.nginx_manifest.write_bytes(b"role-generation")
        self.nginx_manifest.chmod(0o600)
        self.nginx_manifest_sha256 = hashlib.sha256(
            b"role-generation"
        ).hexdigest()
        self.nginx_archive = self.root / "nginx.tar"
        self.nginx_archive.write_bytes(b"archive")
        self.nginx_archive.chmod(0o600)
        self.coordinated_receipt = MODULE.coordinated_receipt_path(
            self.binding,
            COORDINATED_RECEIPT_SHA256,
            secret_root=self.secret_root,
        )
        self.live_lease_claim = MODULE.live_lease_claim_path(
            self.binding,
            LIVE_LEASE_CLAIM_SHA256,
            secret_root=self.secret_root,
        )
        self.claim_document = {
            "owner_action": MODULE.CAPTURE_OWNER_ACTION,
            "claim_epoch": 1,
            "previous_claim_sha256": MODULE.ZERO_SHA256,
            "legacy_frozen_receipt_path": str(
                self.coordinated_receipt
            ),
            "legacy_frozen_receipt_sha256": (
                COORDINATED_RECEIPT_SHA256
            ),
        }
        self.claim_patcher = mock.patch.object(
            MODULE,
            "_load_live_lease_claim",
            side_effect=lambda *_args, **_kwargs: dict(
                self.claim_document
            ),
        )
        self.claim_patcher.start()

    def tearDown(self) -> None:
        self.claim_patcher.stop()
        self.temporary.cleanup()

    def _nginx_result(self) -> dict:
        return {
            "schema": "production-shadow-nginx-host-readback-v1",
            "status": "read-back",
            "operation_id": OPERATION_ID,
            "role": self.binding.role,
            "expected_host": "65.109.216.187",
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "manifest_sha256": self.nginx_manifest_sha256,
            "state": "legacy-frozen",
            "generation_sha256": "e" * 64,
            "journal_sha256": "f" * 64,
            "active_configuration_mutated": False,
            "service_reloaded": False,
        }

    def _receipt(self) -> dict:
        return {
            "role_bindings": {
                self.binding.role: {
                    "manifest_sha256": self.nginx_manifest_sha256,
                },
            },
            "readbacks": {
                self.binding.role: self._nginx_result(),
            },
            "global_generation_sha256": GLOBAL_FREEZE_SHA256,
        }

    def _arguments(
        self,
        action: str,
        *,
        apply: bool,
        receipt_sha256: str = COORDINATED_RECEIPT_SHA256,
        claim_sha256: str = LIVE_LEASE_CLAIM_SHA256,
        claim_owner_action: str | None = None,
    ) -> dict:
        self.claim_document["owner_action"] = (
            claim_owner_action
            if claim_owner_action is not None
            else (
                MODULE.RESTORE_OWNER_ACTION
                if action == "restore"
                else MODULE.CAPTURE_OWNER_ACTION
            )
        )
        receipt = MODULE.coordinated_receipt_path(
            self.binding,
            receipt_sha256,
            secret_root=self.secret_root,
        )
        claim = MODULE.live_lease_claim_path(
            self.binding,
            claim_sha256,
            secret_root=self.secret_root,
        )
        checkpoint_exchange = None
        if apply and action == "restore":
            checkpoint_exchange = lambda challenge: (
                MODULE.controller_checkpoint_response(
                    challenge,
                    live_lease_verify=lambda: {
                        "controller_lock_authority_observed": True,
                    },
                    expected_operation_id=OPERATION_ID,
                    expected_release_sha=RELEASE_SHA,
                    expected_role=self.binding.role,
                    expected_claim_sha256=claim_sha256,
                    expected_claim_epoch=self.claim_document[
                        "claim_epoch"
                    ],
                )
            )
        return {
            "binding_path": self.fixture.path,
            "action": action,
            "release_tree_sha": RELEASE_TREE_SHA,
            "nginx_aggregate_sha256": NGINX_AGGREGATE_SHA256,
            "nginx_manifest": self.nginx_manifest,
            "nginx_manifest_sha256": self.nginx_manifest_sha256,
            "nginx_archive": self.nginx_archive,
            "coordinated_state_receipt": receipt,
            "coordinated_state_receipt_sha256": receipt_sha256,
            "live_lease_claim": claim,
            "live_lease_claim_sha256": claim_sha256,
            "apply": apply,
            "confirm": (
                MODULE.confirmation_phrase(
                    action,
                    self.binding,
                    nginx_aggregate_sha256=NGINX_AGGREGATE_SHA256,
                    nginx_manifest_sha256=self.nginx_manifest_sha256,
                    coordinated_state_receipt_sha256=(
                        receipt_sha256
                    ),
                    live_lease_claim_sha256=claim_sha256,
                )
                if apply and action in {"freeze", "restore"}
                else None
            ),
            "secret_root": self.secret_root,
            "sleep_fn": lambda _seconds: None,
            "proc_root": self.root / "proc",
            "checkpoint_exchange": checkpoint_exchange,
        }

    def test_default_plan_does_not_contact_docker_or_nginx(self):
        with (
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                side_effect=AssertionError("plan contacted Nginx"),
            ),
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                side_effect=AssertionError("plan loaded coordinator receipt"),
            ),
            mock.patch.object(
                SOURCE,
                "inspect_source",
                side_effect=AssertionError("plan contacted Docker"),
            ),
        ):
            plan = MODULE.execute(**self._arguments("freeze", apply=False))
        self.assertEqual(plan["schema"], MODULE.PLAN_SCHEMA)
        self.assertEqual(
            plan["writer_containers"],
            [
                "trading_bot_app",
                "trading_bot_bot",
                "trading_bot_sync_worker",
            ],
        )
        self.assertEqual(
            plan["data_containers_preserved"],
            ["trading_bot_db", "trading_bot_redis"],
        )
        self.assertFalse(plan["production_mutated"])
        self.assertEqual(
            plan["coordinated_state_receipt_sha256"],
            COORDINATED_RECEIPT_SHA256,
        )
        self.assertEqual(
            plan["nginx_aggregate_sha256"],
            NGINX_AGGREGATE_SHA256,
        )
        self.assertEqual(
            plan["required_confirmation"],
            MODULE.confirmation_phrase(
                "freeze",
                self.binding,
                nginx_aggregate_sha256=NGINX_AGGREGATE_SHA256,
                nginx_manifest_sha256=self.nginx_manifest_sha256,
                coordinated_state_receipt_sha256=(
                    COORDINATED_RECEIPT_SHA256
                ),
                live_lease_claim_sha256=LIVE_LEASE_CLAIM_SHA256,
            ),
        )

    def test_writer_identity_is_exact_and_does_not_emit_environment(self):
        document = {
            "Id": "3" * 64,
            "Name": "/trading_bot_app",
            "Image": "sha256:" + "6" * 64,
            "RestartCount": 0,
            "State": {
                "Running": True,
                "Paused": False,
                "Restarting": False,
                "Dead": False,
            },
            "Config": {
                "Image": self.binding.images["application"],
                "Labels": {
                    "com.docker.compose.project": "trading_bot",
                    "com.docker.compose.service": "app",
                    "com.docker.compose.oneoff": "False",
                },
                "Env": [
                    f"RELEASE_SHA={LEGACY_RELEASE_SHA}",
                    "TRADING_BOT_SERVICE=api",
                    "BOT_TOKEN=must-not-appear",
                ],
            },
        }
        identity, running = MODULE._writer_identity(
            document,
            binding=self.binding,
            source_image_id="sha256:" + "6" * 64,
            kind="application",
            expected_name="trading_bot_app",
            expected_service="app",
        )
        self.assertTrue(running)
        self.assertEqual(set(identity), MODULE.WRITER_IDENTITY_FIELDS)
        self.assertNotIn("must-not-appear", json.dumps(identity))

        document["Config"]["Labels"]["com.docker.compose.project"] = "staging"
        with self.assertRaisesRegex(
            MODULE.LegacyWriterFreezeError,
            "identity differs",
        ):
            MODULE._writer_identity(
                document,
                binding=self.binding,
                source_image_id="sha256:" + "6" * 64,
                kind="application",
                expected_name="trading_bot_app",
                expected_service="app",
            )

    def test_coordinated_receipt_path_digest_and_loader_are_exact(self):
        payload = b"coordinated-receipt-fixture"
        digest = hashlib.sha256(payload).hexdigest()
        path = MODULE.coordinated_receipt_path(
            self.binding,
            digest,
            secret_root=self.secret_root,
        )
        path.parent.mkdir(parents=True, mode=0o700)
        path.parent.parent.chmod(0o700)
        path.parent.chmod(0o700)
        path.write_bytes(payload)
        path.chmod(0o600)
        expected = {
            "state": "legacy-frozen",
            "global_generation_sha256": GLOBAL_FREEZE_SHA256,
        }
        with mock.patch.object(
            MODULE.NGINX_COORDINATOR,
            "load_state_receipt",
            return_value=(expected, digest),
        ) as loader:
            receipt, observed = MODULE._load_coordinated_receipt(
                path,
                binding=self.binding,
                release_tree_sha=RELEASE_TREE_SHA,
                nginx_aggregate_sha256=NGINX_AGGREGATE_SHA256,
                expected_sha256=digest,
                secret_root=self.secret_root,
            )
        self.assertEqual(receipt, expected)
        self.assertEqual(observed, digest)
        loader.assert_called_once_with(
            path,
            "legacy-frozen",
            OPERATION_ID,
            RELEASE_SHA,
            RELEASE_TREE_SHA,
            NGINX_AGGREGATE_SHA256,
        )
        with self.assertRaisesRegex(
            MODULE.LegacyWriterFreezeError,
            "path is not canonical",
        ):
            MODULE._load_coordinated_receipt(
                self.root / "copied-receipt.json",
                binding=self.binding,
                release_tree_sha=RELEASE_TREE_SHA,
                nginx_aggregate_sha256=NGINX_AGGREGATE_SHA256,
                expected_sha256=digest,
                secret_root=self.secret_root,
            )

    def test_readiness_rejects_malformed_process_and_health_surfaces(self):
        runtime = FakeRuntime(self.binding)
        malformed_outputs = (
            "123",
            "PID COMMAND\n123 python",
            "1000",
            "1000 \t",
        )
        with mock.patch.object(
            SOURCE,
            "_inspect_required",
            side_effect=runtime.inspect,
        ):
            for output in malformed_outputs:
                with (
                    self.subTest(output=output),
                    self.assertRaisesRegex(
                        MODULE.LegacyWriterFreezeError,
                        "process readback is invalid",
                    ),
                ):
                    MODULE._writer_readiness_sample(
                        self.binding,
                        runtime.writers,
                        runner=lambda _arguments, _timeout: output,
                    )

        def configured_without_state(kind: str, identity: str):
            document = runtime.inspect(kind, identity)
            document["Config"]["Healthcheck"] = {
                "Test": ["CMD", "health-probe"],
            }
            return document

        with (
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=configured_without_state,
            ),
            self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "readiness state differs",
            ),
        ):
            MODULE._writer_readiness_sample(
                self.binding,
                runtime.writers,
                runner=runtime.runner,
            )

    def test_readiness_rechecks_http_at_the_final_stable_sample(self):
        runtime = FakeRuntime(self.binding)
        curl_calls = 0

        def runner(arguments, timeout: int) -> str:
            nonlocal curl_calls
            if arguments[0] == MODULE.CURL:
                curl_calls += 1
                if curl_calls >= 4:
                    raise MODULE.LegacyWriterFreezeError(
                        "application readiness drift"
                    )
            return runtime.runner(arguments, timeout)

        with (
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "did not become stably ready",
            ),
        ):
            MODULE._await_writer_readiness(
                self.binding,
                runtime.writers,
                runner=runner,
                sleep_fn=lambda _seconds: None,
            )
        self.assertGreaterEqual(curl_calls, 4)

    def test_writable_fd_scan_detects_only_write_access_under_source_roots(self):
        proc = self.root / "proc"
        target = self.root / "volume" / "audit.jsonl"
        target.parent.mkdir(parents=True)
        target.write_text("x", encoding="ascii")
        pid = proc / "123"
        (pid / "fd").mkdir(parents=True)
        (pid / "fdinfo").mkdir()
        (pid / "maps").write_text("", encoding="ascii")
        os.symlink(target, pid / "fd" / "4")
        (pid / "fdinfo" / "4").write_text(
            "pos:\t0\nflags:\t0100002\n",
            encoding="ascii",
        )
        self.assertEqual(
            MODULE._host_file_mutator_processes(
                (str(target.parent),),
                proc_root=proc,
            ),
            {123},
        )
        (pid / "fdinfo" / "4").write_text(
            "pos:\t0\nflags:\t0100000\n",
            encoding="ascii",
        )
        self.assertEqual(
            MODULE._host_file_mutator_processes(
                (str(target.parent),),
                proc_root=proc,
            ),
            set(),
        )

    def test_freeze_verify_and_restore_use_only_captured_container_ids(self):
        runtime = FakeRuntime(self.binding)
        (self.root / "proc").mkdir()
        zero = {
            "legacy_writer_process_count": 0,
            "writer_database_client_count": 0,
            "file_mutator_process_count": 0,
        }
        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                return_value=(
                    self._receipt(),
                    COORDINATED_RECEIPT_SHA256,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                return_value=self._nginx_result(),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            mock.patch.object(
                MODULE,
                "_zero_writer_readback",
                return_value=zero,
            ),
            mock.patch.object(
                MODULE,
                "_installed_nginx_freeze_readback",
                return_value=self._nginx_result(),
            ),
        ):
            frozen = MODULE.execute(
                **self._arguments("freeze", apply=True),
                runner=runtime.runner,
            )
            self.assertEqual(frozen["status"], "frozen")
            self.assertEqual(
                frozen["role_freeze_generation_sha256"],
                "e" * 64,
            )
            self.assertEqual(
                frozen["freeze_generation_sha256"],
                GLOBAL_FREEZE_SHA256,
            )
            self.assertFalse(any(runtime.running.values()))
            self.assertEqual(
                [call[1] for call in runtime.calls],
                ["stop", "stop", "stop"],
            )
            self.assertTrue(
                all(call[-1] in {
                    row["id"] for row in runtime.writers.values()
                } for call in runtime.calls)
            )
            verified = MODULE.execute(
                **self._arguments("verify", apply=True),
                runner=runtime.runner,
            )
            self.assertEqual(verified["status"], "verified-frozen")
            evidence_path = (
                MODULE.state_directory(
                    self.binding,
                    secret_root=self.secret_root,
                )
                / MODULE.EVIDENCE_FILENAME
            )
            evidence, digest = SOURCE.load_freeze_evidence(
                evidence_path,
                self.binding,
                source_container_ids={
                    "database": "7" * 64,
                    "application": "3" * 64,
                    "redis": "8" * 64,
                },
            )
            self.assertEqual(
                evidence["freeze_generation_sha256"],
                GLOBAL_FREEZE_SHA256,
            )
            self.assertEqual(
                evidence["live_lease_claim_sha256"],
                LIVE_LEASE_CLAIM_SHA256,
            )
            self.assertEqual(frozen["freeze_evidence_sha256"], digest)
            with MODULE.hold_verified_freeze(
                self.binding,
                freeze_path=evidence_path,
                live_lease_claim=self.live_lease_claim,
                live_lease_claim_sha256=LIVE_LEASE_CLAIM_SHA256,
                secret_root=self.secret_root,
                runner=runtime.runner,
                sleep_fn=lambda _seconds: None,
                proc_root=self.root / "proc",
            ) as verify_live:
                self.assertEqual(verify_live(), zero)
                runtime.running["bot"] = True
                with self.assertRaisesRegex(
                    MODULE.LegacyWriterFreezeError,
                    "runtime identity or state differs",
                ):
                    verify_live()
                runtime.running["bot"] = False
            restored = MODULE.execute(
                **self._arguments("restore", apply=True),
                runner=runtime.runner,
            )
        self.assertEqual(restored["status"], "restored-ready")
        self.assertTrue(restored["legacy_ready_for_nginx_restore"])
        self.assertEqual(restored["stable_sample_count"], 3)
        self.assertTrue(all(runtime.running.values()))
        checkpoints = [
            entry["challenge"]["checkpoint"]
            for entry in restored["interactive_lease_transcript"]
        ]
        self.assertEqual(checkpoints[-1], "before-result")
        self.assertEqual(
            [
                checkpoint
                for checkpoint in checkpoints
                if checkpoint.startswith(
                    ("before-start:", "after-start:")
                )
            ],
            [
                checkpoint
                for kind, _name, _service in MODULE.ROLE_WRITERS[
                    self.binding.role
                ]
                for checkpoint in (
                    f"before-start:{kind}",
                    f"after-start:{kind}",
                )
            ],
        )
        first_readiness = next(
            index
            for index, checkpoint in enumerate(checkpoints)
            if checkpoint.startswith("readiness-")
        )
        self.assertFalse(
            any(
                checkpoint.startswith(
                    ("before-start:", "after-start:")
                )
                for checkpoint in checkpoints[first_readiness:]
            )
        )
        self.assertEqual(
            [
                call[1]
                for call in runtime.calls
                if call[0] == MODULE.DOCKER
                and call[1] in {"stop", "start"}
            ],
            ["stop", "stop", "stop", "start", "start", "start"],
        )
        self.assertFalse(evidence_path.exists())
        self.assertTrue(restored["freeze_evidence_revoked"])

    def test_failed_zero_writer_proof_leaves_writers_stopped_for_reconciliation(
        self,
    ):
        runtime = FakeRuntime(self.binding)
        (self.root / "proc").mkdir()
        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                return_value=(
                    self._receipt(),
                    COORDINATED_RECEIPT_SHA256,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                return_value=self._nginx_result(),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            mock.patch.object(
                MODULE,
                "_zero_writer_readback",
                side_effect=MODULE.LegacyWriterFreezeError(
                    "zero writer failed"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "require explicit reconciliation",
            ):
                MODULE.execute(
                    **self._arguments("freeze", apply=True),
                    runner=runtime.runner,
                )
        self.assertFalse(any(runtime.running.values()))
        journal_path = (
            MODULE.state_directory(
                self.binding,
                secret_root=self.secret_root,
            )
            / MODULE.JOURNAL_FILENAME
        )
        journal = MODULE._strict_json(
            journal_path.read_bytes(),
            label="journal",
        )
        self.assertEqual(journal["status"], "reconciliation-required")
        self.assertEqual(
            journal["coordinated_state_receipt_history"],
            [COORDINATED_RECEIPT_SHA256],
        )
        self.assertEqual(
            journal["live_lease_claim_history"],
            [LIVE_LEASE_CLAIM_SHA256],
        )
        self.assertEqual(
            journal["stopped"],
            ["application", "bot", "sync_worker"],
        )
        self.assertNotEqual(journal["last_error_sha256"], MODULE.ZERO_SHA256)
        self.assertEqual(
            journal["failure_history"][-1],
            journal["last_error_sha256"],
        )
        self.assertEqual(
            [call[1] for call in runtime.calls],
            ["stop", "stop", "stop"],
        )

    def test_restore_readiness_failure_stops_all_before_retry(self):
        runtime = FakeRuntime(self.binding)
        (self.root / "proc").mkdir()
        zero = {
            "legacy_writer_process_count": 0,
            "writer_database_client_count": 0,
            "file_mutator_process_count": 0,
        }
        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                return_value=(
                    self._receipt(),
                    COORDINATED_RECEIPT_SHA256,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                return_value=self._nginx_result(),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            mock.patch.object(
                MODULE,
                "_zero_writer_readback",
                return_value=zero,
            ),
        ):
            MODULE.execute(
                **self._arguments("freeze", apply=True),
                runner=runtime.runner,
            )
            runtime.application_ready = False
            with self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "did not become HTTP-ready",
            ):
                MODULE.execute(
                    **self._arguments("restore", apply=True),
                    runner=runtime.runner,
                )
            self.assertFalse(any(runtime.running.values()))
            directory = MODULE.state_directory(
                self.binding,
                secret_root=self.secret_root,
            )
            self.assertFalse((directory / MODULE.EVIDENCE_FILENAME).exists())
            journal = MODULE._strict_json(
                (directory / MODULE.JOURNAL_FILENAME).read_bytes(),
                label="journal",
            )
            self.assertEqual(journal["status"], "reconciliation-required")
            self.assertNotEqual(
                journal["last_error_sha256"],
                MODULE.ZERO_SHA256,
            )
            failure_history = list(journal["failure_history"])
            starts_before_resume = [
                call for call in runtime.calls if call[1] == "start"
            ]
            runtime.application_ready = True
            restored = MODULE.execute(
                **self._arguments("restore", apply=True),
                runner=runtime.runner,
            )
        self.assertEqual(restored["status"], "restored-ready")
        self.assertTrue(restored["legacy_ready_for_nginx_restore"])
        self.assertEqual(
            len([call for call in runtime.calls if call[1] == "start"]),
            len(starts_before_resume) + len(runtime.writers),
        )
        journal = MODULE._strict_json(
            (directory / MODULE.JOURNAL_FILENAME).read_bytes(),
            label="journal",
        )
        self.assertEqual(journal["status"], "active")
        self.assertEqual(journal["last_error_sha256"], MODULE.ZERO_SHA256)
        self.assertEqual(
            journal["failure_history"][: len(failure_history)],
            failure_history,
        )

    def test_nginx_drift_never_restarts_stopped_writers(self):
        runtime = FakeRuntime(self.binding)
        (self.root / "proc").mkdir()
        nginx_readbacks = [
            *[self._nginx_result() for _ in range(7)],
            {
                **self._nginx_result(),
                "generation_sha256": "f" * 64,
            },
        ]
        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                return_value=(
                    self._receipt(),
                    COORDINATED_RECEIPT_SHA256,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                side_effect=nginx_readbacks,
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            mock.patch.object(
                MODULE,
                "_zero_writer_readback",
                return_value={
                    "legacy_writer_process_count": 0,
                    "writer_database_client_count": 0,
                    "file_mutator_process_count": 0,
                },
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "require explicit reconciliation",
            ):
                MODULE.execute(
                    **self._arguments("freeze", apply=True),
                    runner=runtime.runner,
                )
        self.assertFalse(any(runtime.running.values()))
        self.assertEqual(
            [call[1] for call in runtime.calls],
            ["stop", "stop", "stop"],
        )
        journal_path = (
            MODULE.state_directory(
                self.binding,
                secret_root=self.secret_root,
            )
            / MODULE.JOURNAL_FILENAME
        )
        journal = MODULE._strict_json(
            journal_path.read_bytes(),
            label="journal",
        )
        self.assertEqual(journal["status"], "reconciliation-required")
        self.assertNotEqual(journal["last_error_sha256"], MODULE.ZERO_SHA256)

    def test_restore_advances_to_fresh_r2_receipt_and_claim_epoch(self):
        runtime = FakeRuntime(self.binding)
        (self.root / "proc").mkdir()
        zero = {
            "legacy_writer_process_count": 0,
            "writer_database_client_count": 0,
            "file_mutator_process_count": 0,
        }
        r2_receipt_sha256 = "2" * 64
        r2_claim_sha256 = "3" * 64
        r2_role_generation_sha256 = "4" * 64
        r2_global_generation_sha256 = "5" * 64
        active_epoch = "r1"

        def nginx_result() -> dict:
            if active_epoch == "r1":
                return self._nginx_result()
            return {
                **self._nginx_result(),
                "generation_sha256": r2_role_generation_sha256,
            }

        def receipt() -> dict:
            return {
                "role_bindings": {
                    self.binding.role: {
                        "manifest_sha256": self.nginx_manifest_sha256,
                    },
                },
                "readbacks": {
                    self.binding.role: nginx_result(),
                },
                "global_generation_sha256": (
                    GLOBAL_FREEZE_SHA256
                    if active_epoch == "r1"
                    else r2_global_generation_sha256
                ),
            }

        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                side_effect=lambda *_args, **_kwargs: (
                    receipt(),
                    (
                        COORDINATED_RECEIPT_SHA256
                        if active_epoch == "r1"
                        else r2_receipt_sha256
                    ),
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                side_effect=lambda **_kwargs: nginx_result(),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            mock.patch.object(
                MODULE,
                "_zero_writer_readback",
                return_value=zero,
            ),
        ):
            MODULE.execute(
                **self._arguments("freeze", apply=True),
                runner=runtime.runner,
            )
            self.assertFalse(any(runtime.running.values()))
            active_epoch = "r2"
            r2_receipt = MODULE.coordinated_receipt_path(
                self.binding,
                r2_receipt_sha256,
                secret_root=self.secret_root,
            )
            self.claim_document = {
                "claim_epoch": 2,
                "previous_claim_sha256": LIVE_LEASE_CLAIM_SHA256,
                "legacy_frozen_receipt_path": str(r2_receipt),
                "legacy_frozen_receipt_sha256": r2_receipt_sha256,
            }
            restored = MODULE.execute(
                **self._arguments(
                    "restore",
                    apply=True,
                    receipt_sha256=r2_receipt_sha256,
                    claim_sha256=r2_claim_sha256,
                ),
                runner=runtime.runner,
            )

        self.assertEqual(restored["status"], "restored-ready")
        self.assertEqual(
            restored["live_lease_claim_sha256"],
            r2_claim_sha256,
        )
        journal_path = (
            MODULE.state_directory(
                self.binding,
                secret_root=self.secret_root,
            )
            / MODULE.JOURNAL_FILENAME
        )
        journal = MODULE._strict_json(
            journal_path.read_bytes(),
            label="journal",
        )
        self.assertEqual(
            journal["coordinated_state_receipt_history"],
            [COORDINATED_RECEIPT_SHA256, r2_receipt_sha256],
        )
        self.assertEqual(
            journal["live_lease_claim_history"],
            [LIVE_LEASE_CLAIM_SHA256, r2_claim_sha256],
        )
        self.assertEqual(journal["live_lease_claim_epoch_history"], [1, 2])
        with self.assertRaisesRegex(
            MODULE.LegacyWriterFreezeError,
            "stale",
        ):
            MODULE._bind_journal_live_lease_epoch(
                journal_path,
                journal,
                action="freeze",
                coordinated_state_receipt_sha256=(
                    COORDINATED_RECEIPT_SHA256
                ),
                live_lease_claim={
                    "claim_epoch": 1,
                    "previous_claim_sha256": MODULE.ZERO_SHA256,
                },
                live_lease_claim_sha256=LIVE_LEASE_CLAIM_SHA256,
                role_freeze_generation_sha256="e" * 64,
                freeze_generation_sha256=GLOBAL_FREEZE_SHA256,
            )

    def test_restore_can_adopt_a_later_claim_on_the_same_receipt(self):
        runtime = FakeRuntime(self.binding)
        directory = MODULE._ensure_private_children(
            self.secret_root / OPERATION_ID,
            self.binding.role,
        )
        journal_path = directory / MODULE.JOURNAL_FILENAME
        journal = MODULE._base_journal(
            self.binding,
            release_tree_sha=RELEASE_TREE_SHA,
            nginx_aggregate_sha256=NGINX_AGGREGATE_SHA256,
            nginx_manifest_sha256=self.nginx_manifest_sha256,
            coordinated_state_receipt_sha256=(
                COORDINATED_RECEIPT_SHA256
            ),
            live_lease_claim_sha256=LIVE_LEASE_CLAIM_SHA256,
            live_lease_claim_epoch=1,
            role_freeze_generation_sha256="e" * 64,
            freeze_generation_sha256=GLOBAL_FREEZE_SHA256,
            source_container_ids={
                "database": "7" * 64,
                "application": "3" * 64,
                "redis": "8" * 64,
            },
            writer_containers=runtime.writers,
        )
        journal["status"] = "frozen"
        journal["state_sha256"] = MODULE._state_hash(journal)
        write_secure_new_bytes(
            journal_path,
            MODULE.canonical_json(journal),
            label="test same-receipt journal",
            mode=0o600,
            max_size=MODULE.MAX_JSON_BYTES,
        )
        later_claim_sha256 = "6" * 64
        MODULE._bind_journal_live_lease_epoch(
            journal_path,
            journal,
            action="restore",
            coordinated_state_receipt_sha256=(
                COORDINATED_RECEIPT_SHA256
            ),
            live_lease_claim={
                "claim_epoch": 3,
                "previous_claim_sha256": "7" * 64,
            },
            live_lease_claim_sha256=later_claim_sha256,
            role_freeze_generation_sha256="e" * 64,
            freeze_generation_sha256=GLOBAL_FREEZE_SHA256,
        )
        self.assertEqual(
            journal["coordinated_state_receipt_history"],
            [COORDINATED_RECEIPT_SHA256],
        )
        self.assertEqual(
            journal["live_lease_claim_history"],
            [LIVE_LEASE_CLAIM_SHA256, later_claim_sha256],
        )
        self.assertEqual(
            journal["live_lease_claim_epoch_history"],
            [1, 3],
        )

    def test_apply_rejects_missing_live_lease_before_docker_mutation(self):
        runtime = FakeRuntime(self.binding)
        with (
            mock.patch.object(
                MODULE,
                "_load_live_lease_claim",
                side_effect=MODULE.LegacyWriterFreezeError(
                    "live lease claim material verification failed"
                ),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=AssertionError("Docker was contacted"),
            ),
            self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "claim material verification failed",
            ),
        ):
            MODULE.execute(
                **self._arguments("freeze", apply=True),
                runner=runtime.runner,
            )
        self.assertEqual(runtime.calls, [])

    def test_restore_without_interactive_protocol_fails_before_docker(self):
        arguments = self._arguments("restore", apply=True)
        arguments["checkpoint_exchange"] = None
        with (
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=AssertionError("Docker was contacted"),
            ),
            self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "interactive controller live lease protocol",
            ),
        ):
            MODULE.execute(**arguments)

    def test_restore_rejects_capture_owner_before_docker(self):
        arguments = self._arguments(
            "restore",
            apply=True,
            claim_owner_action=MODULE.CAPTURE_OWNER_ACTION,
        )
        with (
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=AssertionError("Docker was contacted"),
            ),
            self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "claim owner action differs",
            ),
        ):
            MODULE.execute(**arguments)

    def test_eof_after_first_start_compensates_all_writers_to_stopped(self):
        runtime = FakeRuntime(self.binding)
        (self.root / "proc").mkdir()
        zero = {
            "legacy_writer_process_count": 0,
            "writer_database_client_count": 0,
            "file_mutator_process_count": 0,
        }
        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                return_value=(
                    self._receipt(),
                    COORDINATED_RECEIPT_SHA256,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                return_value=self._nginx_result(),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            mock.patch.object(
                MODULE,
                "_zero_writer_readback",
                return_value=zero,
            ),
        ):
            MODULE.execute(
                **self._arguments("freeze", apply=True),
                runner=runtime.runner,
            )
            exchanges = 0

            def disconnect_after_first_start(challenge):
                nonlocal exchanges
                exchanges += 1
                if challenge["checkpoint"] == "after-start:application":
                    raise EOFError("controller connection closed")
                return MODULE.controller_checkpoint_response(
                    challenge,
                    live_lease_verify=lambda: {
                        "controller_lock_authority_observed": True,
                    },
                    expected_operation_id=OPERATION_ID,
                    expected_release_sha=RELEASE_SHA,
                    expected_role=self.binding.role,
                    expected_claim_sha256=LIVE_LEASE_CLAIM_SHA256,
                    expected_claim_epoch=1,
                )

            arguments = self._arguments("restore", apply=True)
            arguments["checkpoint_exchange"] = (
                disconnect_after_first_start
            )
            with self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "interactive live lease exchange failed",
            ):
                MODULE.execute(**arguments, runner=runtime.runner)
        self.assertEqual(exchanges, 2)
        self.assertFalse(any(runtime.running.values()))
        starts = [
            call for call in runtime.calls
            if len(call) > 1 and call[1] == "start"
        ]
        self.assertEqual(len(starts), 1)
        journal = MODULE._strict_json(
            (
                MODULE.state_directory(
                    self.binding,
                    secret_root=self.secret_root,
                )
                / MODULE.JOURNAL_FILENAME
            ).read_bytes(),
            label="journal",
        )
        self.assertEqual(journal["status"], "reconciliation-required")
        self.assertEqual(
            journal["stopped"],
            sorted(runtime.writers),
        )
        self.assertFalse(
            journal["interactive_lease_authority_handoff_complete"]
        )

    def test_restore_reestablishes_all_stopped_after_sigkill_like_crashes(self):
        runtime = FakeRuntime(self.binding)
        (self.root / "proc").mkdir()
        zero = {
            "legacy_writer_process_count": 0,
            "writer_database_client_count": 0,
            "file_mutator_process_count": 0,
        }
        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                return_value=(
                    self._receipt(),
                    COORDINATED_RECEIPT_SHA256,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                return_value=self._nginx_result(),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            mock.patch.object(
                MODULE,
                "_zero_writer_readback",
                return_value=zero,
            ) as zero_proof,
        ):
            MODULE.execute(
                **self._arguments("freeze", apply=True),
                runner=runtime.runner,
            )
            directory = MODULE.state_directory(
                self.binding,
                secret_root=self.secret_root,
            )
            journal_path = directory / MODULE.JOURNAL_FILENAME

            runtime.running["application"] = True
            journal = MODULE._strict_json(
                journal_path.read_bytes(),
                label="one-writer crash journal",
            )
            journal["status"] = "restoring"
            MODULE._write_journal(journal_path, journal)
            runtime.calls.clear()
            resumed_one = MODULE.execute(
                **self._arguments("restore", apply=True),
                runner=runtime.runner,
            )
            one_checkpoints = [
                row["challenge"]["checkpoint"]
                for row in resumed_one["interactive_lease_transcript"]
            ]
            self.assertEqual(
                one_checkpoints[:2],
                [
                    "before-stop:application",
                    "after-stop:application",
                ],
            )
            self.assertEqual(
                [
                    call[1]
                    for call in runtime.calls
                    if call[0] == MODULE.DOCKER
                    and call[1] in {"stop", "start"}
                ],
                ["stop", "start", "start", "start"],
            )

            journal = MODULE._strict_json(
                journal_path.read_bytes(),
                label="all-writer crash journal",
            )
            journal["status"] = "restoring"
            MODULE._write_journal(journal_path, journal)
            runtime.calls.clear()
            resumed_all = MODULE.execute(
                **self._arguments("restore", apply=True),
                runner=runtime.runner,
            )
            all_checkpoints = [
                row["challenge"]["checkpoint"]
                for row in resumed_all["interactive_lease_transcript"]
            ]
            expected_stop = [
                checkpoint
                for kind in runtime.writers
                for checkpoint in (
                    f"before-stop:{kind}",
                    f"after-stop:{kind}",
                )
            ]
            self.assertEqual(
                all_checkpoints[: len(expected_stop)],
                expected_stop,
            )
            self.assertEqual(
                [
                    call[1]
                    for call in runtime.calls
                    if call[0] == MODULE.DOCKER
                    and call[1] in {"stop", "start"}
                ],
                ["stop"] * len(runtime.writers)
                + ["start"] * len(runtime.writers),
            )

            runtime.calls.clear()
            resumed_active = MODULE.execute(
                **self._arguments("restore", apply=True),
                runner=runtime.runner,
            )
        self.assertEqual(resumed_active["status"], "restored-ready")
        self.assertTrue(all(runtime.running.values()))
        self.assertEqual(
            [
                row["challenge"]["checkpoint"]
                for row in resumed_active["interactive_lease_transcript"]
            ][: len(expected_stop)],
            expected_stop,
        )
        self.assertEqual(
            [
                call[1]
                for call in runtime.calls
                if call[0] == MODULE.DOCKER
                and call[1] in {"stop", "start"}
            ],
            ["stop"] * len(runtime.writers)
            + ["start"] * len(runtime.writers),
        )
        self.assertEqual(zero_proof.call_count, 4)

    def test_consumed_claim_cannot_stop_active_writers(self):
        runtime = FakeRuntime(self.binding)
        (self.root / "proc").mkdir()
        zero = {
            "legacy_writer_process_count": 0,
            "writer_database_client_count": 0,
            "file_mutator_process_count": 0,
        }
        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                return_value=(
                    self._receipt(),
                    COORDINATED_RECEIPT_SHA256,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                return_value=self._nginx_result(),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            mock.patch.object(
                MODULE,
                "_zero_writer_readback",
                return_value=zero,
            ),
        ):
            MODULE.execute(
                **self._arguments("freeze", apply=True),
                runner=runtime.runner,
            )
            MODULE.execute(
                **self._arguments("restore", apply=True),
                runner=runtime.runner,
            )
            self.assertTrue(all(runtime.running.values()))
            runtime.calls.clear()
            arguments = self._arguments("restore", apply=True)

            def consumed_claim(_challenge):
                raise RuntimeError("live lease claim has already been consumed")

            arguments["checkpoint_exchange"] = consumed_claim
            with self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "interactive live lease exchange failed",
            ):
                MODULE.execute(**arguments, runner=runtime.runner)
        self.assertTrue(all(runtime.running.values()))
        self.assertFalse(
            any(
                call[0] == MODULE.DOCKER
                and call[1] in {"stop", "start"}
                for call in runtime.calls
            )
        )

    def test_verify_rejects_c1_evidence_after_journal_advances_to_c2(self):
        runtime = FakeRuntime(self.binding)
        (self.root / "proc").mkdir()
        zero = {
            "legacy_writer_process_count": 0,
            "writer_database_client_count": 0,
            "file_mutator_process_count": 0,
        }
        later_claim_sha256 = "6" * 64
        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                return_value=(
                    self._receipt(),
                    COORDINATED_RECEIPT_SHA256,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                return_value=self._nginx_result(),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            mock.patch.object(
                MODULE,
                "_zero_writer_readback",
                return_value=zero,
            ),
        ):
            MODULE.execute(
                **self._arguments("freeze", apply=True),
                runner=runtime.runner,
            )
            journal_path = (
                MODULE.state_directory(
                    self.binding,
                    secret_root=self.secret_root,
                )
                / MODULE.JOURNAL_FILENAME
            )
            journal = MODULE._strict_json(
                journal_path.read_bytes(),
                label="journal",
            )
            MODULE._bind_journal_live_lease_epoch(
                journal_path,
                journal,
                action="restore",
                coordinated_state_receipt_sha256=(
                    COORDINATED_RECEIPT_SHA256
                ),
                live_lease_claim={
                    "claim_epoch": 2,
                    "previous_claim_sha256": LIVE_LEASE_CLAIM_SHA256,
                },
                live_lease_claim_sha256=later_claim_sha256,
                role_freeze_generation_sha256="e" * 64,
                freeze_generation_sha256=GLOBAL_FREEZE_SHA256,
            )
            self.claim_document = {
                "claim_epoch": 2,
                "previous_claim_sha256": LIVE_LEASE_CLAIM_SHA256,
                "legacy_frozen_receipt_path": str(
                    self.coordinated_receipt
                ),
                "legacy_frozen_receipt_sha256": (
                    COORDINATED_RECEIPT_SHA256
                ),
            }
            with self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "freeze evidence verification failed",
            ):
                MODULE.execute(
                    **self._arguments(
                        "verify",
                        apply=True,
                        claim_sha256=later_claim_sha256,
                    ),
                    runner=runtime.runner,
                )

    def test_restore_rejects_a_prepared_journal(self):
        runtime = FakeRuntime(self.binding)
        directory = MODULE._ensure_private_children(
            self.secret_root / OPERATION_ID,
            self.binding.role,
        )
        journal = MODULE._base_journal(
            self.binding,
            release_tree_sha=RELEASE_TREE_SHA,
            nginx_aggregate_sha256=NGINX_AGGREGATE_SHA256,
            nginx_manifest_sha256=self.nginx_manifest_sha256,
            coordinated_state_receipt_sha256=(
                COORDINATED_RECEIPT_SHA256
            ),
            live_lease_claim_sha256=LIVE_LEASE_CLAIM_SHA256,
            live_lease_claim_epoch=1,
            role_freeze_generation_sha256="e" * 64,
            freeze_generation_sha256=GLOBAL_FREEZE_SHA256,
            source_container_ids={
                "database": "7" * 64,
                "application": "3" * 64,
                "redis": "8" * 64,
            },
            writer_containers=runtime.writers,
        )
        write_secure_new_bytes(
            directory / MODULE.JOURNAL_FILENAME,
            MODULE.canonical_json(journal),
            label="test prepared journal",
            mode=0o600,
            max_size=MODULE.MAX_JSON_BYTES,
        )
        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                return_value=(
                    self._receipt(),
                    COORDINATED_RECEIPT_SHA256,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                return_value=self._nginx_result(),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            self.assertRaisesRegex(
                MODULE.LegacyWriterFreezeError,
                "journal is not restorable",
            ),
        ):
            MODULE.execute(
                **self._arguments("restore", apply=True),
                runner=runtime.runner,
            )
        self.assertEqual(runtime.calls, [])
        persisted = MODULE._strict_json(
            (directory / MODULE.JOURNAL_FILENAME).read_bytes(),
            label="journal",
        )
        self.assertEqual(persisted["status"], "prepared")

    def test_freezing_journal_resumes_after_process_interruption(self):
        runtime = FakeRuntime(self.binding)
        runtime.running["application"] = False
        (self.root / "proc").mkdir()
        directory = MODULE._ensure_private_children(
            self.secret_root / OPERATION_ID,
            self.binding.role,
        )
        journal = MODULE._base_journal(
            self.binding,
            release_tree_sha=RELEASE_TREE_SHA,
            nginx_aggregate_sha256=NGINX_AGGREGATE_SHA256,
            nginx_manifest_sha256=self.nginx_manifest_sha256,
            coordinated_state_receipt_sha256=(
                COORDINATED_RECEIPT_SHA256
            ),
            live_lease_claim_sha256=LIVE_LEASE_CLAIM_SHA256,
            live_lease_claim_epoch=1,
            role_freeze_generation_sha256="e" * 64,
            freeze_generation_sha256=GLOBAL_FREEZE_SHA256,
            source_container_ids={
                "database": "7" * 64,
                "application": "3" * 64,
                "redis": "8" * 64,
            },
            writer_containers=runtime.writers,
        )
        journal["status"] = "freezing"
        journal["stopped"] = ["application"]
        journal["sequence"] = 1
        journal["state_sha256"] = MODULE._state_hash(journal)
        write_secure_new_bytes(
            directory / MODULE.JOURNAL_FILENAME,
            MODULE.canonical_json(journal),
            label="test journal",
            mode=0o600,
            max_size=MODULE.MAX_JSON_BYTES,
        )
        with (
            mock.patch.object(
                MODULE,
                "_load_coordinated_receipt",
                return_value=(
                    self._receipt(),
                    COORDINATED_RECEIPT_SHA256,
                ),
            ),
            mock.patch.object(
                MODULE,
                "_nginx_readback",
                return_value=self._nginx_result(),
            ),
            mock.patch.object(
                MODULE,
                "_refresh_runtime",
                side_effect=runtime.refresh,
            ),
            mock.patch.object(
                SOURCE,
                "_inspect_required",
                side_effect=runtime.inspect,
            ),
            mock.patch.object(
                MODULE,
                "_zero_writer_readback",
                return_value={
                    "legacy_writer_process_count": 0,
                    "writer_database_client_count": 0,
                    "file_mutator_process_count": 0,
                },
            ),
        ):
            result = MODULE.execute(
                **self._arguments("freeze", apply=True),
                runner=runtime.runner,
            )
        self.assertEqual(result["status"], "frozen")
        self.assertEqual(
            [call[-1] for call in runtime.calls],
            [
                runtime.writers["bot"]["id"],
                runtime.writers["sync_worker"]["id"],
            ],
        )

    def test_journal_rejects_tampering_and_unknown_stopped_kind(self):
        journal = MODULE._base_journal(
            self.binding,
            release_tree_sha=RELEASE_TREE_SHA,
            nginx_aggregate_sha256=NGINX_AGGREGATE_SHA256,
            nginx_manifest_sha256=self.nginx_manifest_sha256,
            coordinated_state_receipt_sha256=(
                COORDINATED_RECEIPT_SHA256
            ),
            live_lease_claim_sha256=LIVE_LEASE_CLAIM_SHA256,
            live_lease_claim_epoch=1,
            role_freeze_generation_sha256="e" * 64,
            freeze_generation_sha256=GLOBAL_FREEZE_SHA256,
            source_container_ids={
                "database": "7" * 64,
                "application": "3" * 64,
                "redis": "8" * 64,
            },
            writer_containers=FakeRuntime(self.binding).writers,
        )
        journal["stopped"] = ["forged"]
        journal["state_sha256"] = MODULE._state_hash(journal)
        with self.assertRaisesRegex(
            MODULE.LegacyWriterFreezeError,
            "binding or state differs",
        ):
            MODULE._validate_journal(
                journal,
                binding=self.binding,
                release_tree_sha=RELEASE_TREE_SHA,
                nginx_aggregate_sha256=NGINX_AGGREGATE_SHA256,
                nginx_manifest_sha256=self.nginx_manifest_sha256,
                coordinated_state_receipt_sha256=(
                    COORDINATED_RECEIPT_SHA256
                ),
                live_lease_claim_sha256=LIVE_LEASE_CLAIM_SHA256,
                live_lease_claim_epoch=1,
                role_freeze_generation_sha256="e" * 64,
                freeze_generation_sha256=GLOBAL_FREEZE_SHA256,
                source_container_ids={
                    "database": "7" * 64,
                    "application": "3" * 64,
                    "redis": "8" * 64,
                },
                writer_containers=FakeRuntime(self.binding).writers,
            )


if __name__ == "__main__":
    unittest.main()
