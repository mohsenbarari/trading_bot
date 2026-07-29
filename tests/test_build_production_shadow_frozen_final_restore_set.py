from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from core.canonical_json import canonical_json_bytes
from core.human_approval import POLICY_SCHEMA, TOKEN_SCHEMA
from core.production_shadow_authorization import (
    APPROVAL_HASH_FIELD,
    AUTHORIZATION_ACTION,
    AUTHORIZATION_ENVIRONMENT,
    POLICY_HASH_FIELD,
    ZERO_SHA256,
    authorization_subject_from_manifest,
)
from scripts import build_production_shadow_frozen_final_restore_set as MODULE
from scripts import orchestrate_production_shadow_nginx_generations as NGINX
from scripts import produce_production_shadow_source_snapshot as SOURCE
from scripts.build_production_shadow_source_snapshot_binding import build_binding
from tests.test_production_shadow_cutover_controller import (
    manifest_payload,
    write_controller_manifest,
)


def secure_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def archive_payload(label: str) -> tuple[bytes, int]:
    content = f"snapshot:{label}\n".encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo("payload.txt")
        info.size = len(content)
        info.mode = 0o600
        info.mtime = 0
        archive.addfile(info, io.BytesIO(content))
    return output.getvalue(), len(content)


class FrozenFinalRestoreSetFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.control_root = root / "controller"
        self.input_root = root / "inputs"
        self.output_root = root / "outputs"
        for path in (self.control_root, self.input_root, self.output_root):
            private_directory(path)

        self.controller = manifest_payload()
        self.private_key = Ed25519PrivateKey.from_private_bytes(b"\x25" * 32)
        public_key = self.private_key.public_key().public_bytes_raw()
        self.policy = {
            "schema": POLICY_SCHEMA,
            "policy_id": str(uuid4()),
            "issuer": {
                "issuer_id": "frozen-final-test-issuer",
                "key_id": "frozen-final-test-key",
                "operator": "owner77",
                "authenticator_id": "frozen-final-test-totp",
                "public_key": base64.b64encode(public_key).decode("ascii"),
            },
            "actions": [
                {
                    "action": AUTHORIZATION_ACTION,
                    "environments": [AUTHORIZATION_ENVIRONMENT],
                    "max_ttl_seconds": 86400,
                }
            ],
        }
        self.policy_bytes = canonical_json_bytes(self.policy)
        self.controller["artifacts"][POLICY_HASH_FIELD] = hashlib.sha256(
            self.policy_bytes
        ).hexdigest()

        self.receipt = self._receipt()
        self.controller["artifacts"]["nginx_freeze_generation_sha256"] = (
            self.receipt["global_generation_sha256"]
        )
        self._write_authorization()
        self._write_controller()
        self.controller_sha256 = hashlib.sha256(
            self.controller_path.read_bytes()
        ).hexdigest()

        self._write_receipt()
        self._write_claim()
        self.sources: dict[str, dict] = {}
        self.freeze_paths: dict[str, Path] = {}
        self.source_paths: dict[str, Path] = {}
        for role in MODULE.SOURCE_ROLES:
            self._write_source(role)

        self._write_ir_transport()

    def _write_authorization(self) -> None:
        template = json.loads(canonical_json_bytes(self.controller))
        template["artifacts"][APPROVAL_HASH_FIELD] = ZERO_SHA256
        now = datetime.now(timezone.utc).replace(microsecond=0)
        unsigned = {
            "schema": TOKEN_SCHEMA,
            "approval_id": str(uuid4()),
            "policy_id": self.policy["policy_id"],
            "policy_hash": hashlib.sha256(self.policy_bytes).hexdigest(),
            "issuer_id": self.policy["issuer"]["issuer_id"],
            "key_id": self.policy["issuer"]["key_id"],
            "operator": self.policy["issuer"]["operator"],
            "authenticator_id": self.policy["issuer"]["authenticator_id"],
            "action": AUTHORIZATION_ACTION,
            "environment": AUTHORIZATION_ENVIRONMENT,
            "subject": authorization_subject_from_manifest(template),
            "issued_at": (
                now - timedelta(seconds=5)
            ).isoformat().replace("+00:00", "Z"),
            "expires_at": (
                now + timedelta(hours=12)
            ).isoformat().replace("+00:00", "Z"),
            "authentication": {"methods": ["password", "totp"]},
        }
        token = {
            **unsigned,
            "signature": base64.b64encode(
                self.private_key.sign(canonical_json_bytes(unsigned))
            ).decode("ascii"),
        }
        self.approval_bytes = canonical_json_bytes(token)
        self.controller["artifacts"][APPROVAL_HASH_FIELD] = hashlib.sha256(
            self.approval_bytes
        ).hexdigest()
        self.approval_path = self.input_root / "approval.json"
        self.policy_path = self.input_root / "policy.json"
        secure_file(self.approval_path, self.approval_bytes)
        secure_file(self.policy_path, self.policy_bytes)

    def _write_controller(self) -> None:
        self.controller_path = self.input_root / "controller.json"
        write_controller_manifest(self.controller_path, self.controller)

    def _receipt(self) -> dict:
        vhost_rows: dict[str, dict] = {}
        role_rows: dict[str, dict[str, str]] = {
            role: {} for role in NGINX.ROLE_ORDER
        }
        global_rows: dict[str, str] = {}
        for index, (vhost, layout) in enumerate(
            NGINX.VHOST_RECEIPT_LAYOUT.items(),
            start=1,
        ):
            generation = hashlib.sha256(
                f"legacy-frozen:{vhost}:{index}".encode()
            ).hexdigest()
            row = {
                "role": layout["role"],
                "destination": layout["destination"],
                "generation_sha256": generation,
            }
            vhost_rows[vhost] = row
            role_rows[row["role"]][row["destination"]] = generation
            global_rows[
                f"{row['role']}:{row['destination']}"
            ] = generation
        role_bindings = {
            role: {
                "expected_host": NGINX.GENERATION.ROLE_HOSTS[role],
                "manifest_sha256": hashlib.sha256(
                    f"manifest:{role}".encode()
                ).hexdigest(),
                "archive_sha256": hashlib.sha256(
                    f"archive:{role}".encode()
                ).hexdigest(),
            }
            for role in NGINX.ROLE_ORDER
        }
        readbacks = {}
        for role in NGINX.ROLE_ORDER:
            binding = role_bindings[role]
            readbacks[role] = {
                "schema": "production-shadow-nginx-host-readback-v1",
                "status": "read-back",
                "operation_id": self.controller["operation_id"],
                "role": role,
                "expected_host": binding["expected_host"],
                "release_sha": self.controller["release_sha"],
                "release_tree_sha": self.controller["release_tree_sha"],
                "manifest_sha256": binding["manifest_sha256"],
                "archive_sha256": binding["archive_sha256"],
                "state": "legacy-frozen",
                "generation_sha256": NGINX.GENERATION._generation_digest(
                    role_rows[role]
                ),
                "enabled_inventory_sha256": hashlib.sha256(
                    f"inventory:{role}".encode()
                ).hexdigest(),
                "enabled_inventory_count": 2 if role == "bot_fi" else 1,
                "active_configuration_mutated": False,
                "service_reloaded": False,
                "journal_sha256": hashlib.sha256(
                    f"journal:{role}".encode()
                ).hexdigest(),
            }
        return {
            "schema": NGINX.STATE_RECEIPT_SCHEMA,
            "verification_status": "verified",
            "source_action": "activate",
            "requested_target_state": "legacy-frozen",
            "coordinator_status": "activated",
            "operation_id": self.controller["operation_id"],
            "release_sha": self.controller["release_sha"],
            "release_tree_sha": self.controller["release_tree_sha"],
            "aggregate_sha256": hashlib.sha256(
                b"coordinated-nginx-aggregate"
            ).hexdigest(),
            "role_bindings": role_bindings,
            "state": "legacy-frozen",
            "vhost_generation_sha256": vhost_rows,
            "global_generation_sha256": (
                NGINX.GENERATION._generation_digest(global_rows)
            ),
            "readbacks": readbacks,
            "external_readback": {
                "states": ["legacy-frozen"],
                "states_by_role": {
                    role: "legacy-frozen" for role in NGINX.ROLE_ORDER
                },
                "blocked_probes_performed": True,
                "write_method_probe_performed": True,
                "vhosts": {
                    vhost: {"get": 200, "post": 503, "websocket": 503}
                    for vhost, _host in NGINX.VHOST_TARGETS
                },
            },
            "journal_sha256": hashlib.sha256(
                b"coordinator-journal"
            ).hexdigest(),
            "evidence_count": 7,
            "evidence_tail_sha256": hashlib.sha256(
                b"coordinator-evidence-tail"
            ).hexdigest(),
            "production_contacted": True,
            "active_configuration_mutated": False,
            "current_mutated": False,
            "container_mutated": False,
            "volume_mutated": False,
            "data_mutated": False,
        }

    def _write_receipt(self) -> None:
        payload = canonical_json_bytes(self.receipt)
        self.receipt_sha256 = hashlib.sha256(payload).hexdigest()
        self.receipt_path = (
            self.control_root
            / self.controller["operation_id"]
            / "nginx-coordinator"
            / "receipts"
            / f"legacy-frozen-{self.receipt_sha256}.json"
        )
        secure_file(self.receipt_path, payload)

    def _binding(self, role: str) -> tuple[dict, str]:
        binding = build_binding(
            self.controller,
            controller_sha256=self.controller_sha256,
            role=role,
            mode="frozen-final",
        )
        return binding, digest(binding)

    def _source_inventory(self, role: str, binding: dict) -> dict:
        images = {
            kind: {
                "reference": reference,
                "image_id": "sha256:"
                + hashlib.sha256(f"{role}:{kind}".encode()).hexdigest(),
            }
            for kind, reference in binding["images"].items()
        }
        volumes = {
            kind: {
                "name": name,
                "driver": "local",
                "mountpoint": f"/var/lib/docker/volumes/{name}/_data",
                "labels_sha256": hashlib.sha256(
                    f"labels:{role}:{kind}".encode()
                ).hexdigest(),
                "options_sha256": hashlib.sha256(
                    f"options:{role}:{kind}".encode()
                ).hexdigest(),
            }
            for kind, name in binding["volumes"].items()
        }
        containers = {}
        identifiers = {}
        for index, kind in enumerate(
            ("database", "application", "redis"),
            start=1,
        ):
            identifier = hashlib.sha256(
                f"container:{role}:{kind}".encode()
            ).hexdigest()
            identifiers[kind] = identifier
            mounts = {
                volume_kind: {
                    "name": binding["volumes"][volume_kind],
                    "source": volumes[volume_kind]["mountpoint"],
                    "destination": destination,
                    "rw": True,
                }
                for volume_kind, destination in SOURCE.SOURCE_MOUNTS[
                    kind
                ].items()
            }
            containers[kind] = {
                "id": identifier,
                "name": binding["containers"][kind],
                "image_id": images[kind]["image_id"],
                "image_reference": binding["images"][kind],
                "project": binding["source_project"],
                "service": SOURCE.SOURCE_SERVICES[kind],
                "running": True,
                "started_at": f"2026-07-28T00:00:0{index}Z",
                "restart_count": 0,
                "mounts": mounts,
                "other_mount_count": 0,
                "other_mounts_sha256": hashlib.sha256(b"[]").hexdigest(),
            }
        public = {
            "containers": containers,
            "images": images,
            "volumes": volumes,
        }
        return {
            **public,
            "identity_sha256": digest(public),
            "container_ids": identifiers,
        }

    def _write_source(self, role: str) -> None:
        binding, binding_sha256 = self._binding(role)
        inventory = self._source_inventory(role, binding)
        final = self.input_root / role / "frozen-final"
        private_directory(final)
        artifacts: dict[str, dict] = {}
        trees: dict[str, str] = {}
        uploads, uploads_expanded = archive_payload(f"uploads:{role}")
        audit, audit_expanded = archive_payload(f"audit:{role}")
        payloads = {
            "database-backup": f"database:{role}".encode(),
            "uploads-archive": uploads,
            "audit-archive": audit,
        }
        expanded = {
            "uploads": uploads_expanded,
            "audit": audit_expanded,
        }
        for kind, payload in payloads.items():
            filename = SOURCE.ARTIFACT_FILES[kind]
            secure_file(final / filename, payload)
            tree = (
                None
                if kind == "database-backup"
                else hashlib.sha256(f"tree:{role}:{kind}".encode()).hexdigest()
            )
            artifacts[kind] = {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "restored_tree_sha256": tree,
            }
            if tree is not None:
                trees[kind.removesuffix("-archive")] = tree
        source_database = {
            "alembic_revision": "base_1",
            "fingerprint_algorithm": (
                "pg-copy-jsonl-sha256-canonical-session-v1"
            ),
            "database_fingerprint_sha256": hashlib.sha256(
                f"database-fingerprint:{role}".encode()
            ).hexdigest(),
            "row_count": 11 if role == "bot_fi" else 17,
            "table_count": 3,
        }
        freeze = {
            "schema": SOURCE.FREEZE_SCHEMA,
            "operation_id": self.controller["operation_id"],
            "release_sha": self.controller["release_sha"],
            "legacy_release_sha": self.controller["legacy_release_sha"],
            "role": role,
            "source_project": binding["source_project"],
            "controller_manifest_sha256": self.controller_sha256,
            "approval_sha256": self.controller["artifacts"][
                APPROVAL_HASH_FIELD
            ],
            "production_vhosts": SOURCE._expected_vhosts(),
            "source_container_ids": inventory["container_ids"],
            "freeze_generation_sha256": self.receipt[
                "global_generation_sha256"
            ],
            "live_lease_claim_sha256": self.claim_sha256,
            "freeze_active": True,
            "write_capable_route_count": 0,
            "legacy_writer_process_count": 0,
            "writer_database_client_count": 0,
            "file_mutator_process_count": 0,
        }
        freeze_path = self.input_root / role / "freeze-evidence.json"
        secure_file(freeze_path, canonical_json_bytes(freeze))
        freeze_sha256 = hashlib.sha256(
            freeze_path.read_bytes()
        ).hexdigest()
        source_public = {
            key: inventory[key] for key in ("containers", "images", "volumes")
        }
        source_public["identity_sha256"] = inventory["identity_sha256"]
        document = {
            "schema": SOURCE.MANIFEST_SCHEMA,
            "status": "source-snapshot-created",
            "operation_id": self.controller["operation_id"],
            "role": role,
            "mode": "frozen-final",
            "release_sha": self.controller["release_sha"],
            "legacy_release_sha": self.controller["legacy_release_sha"],
            "source_project": binding["source_project"],
            "controller_manifest_sha256": self.controller_sha256,
            "approval_sha256": self.controller["artifacts"][
                APPROVAL_HASH_FIELD
            ],
            "binding_sha256": binding_sha256,
            "freeze_evidence_sha256": freeze_sha256,
            "source": source_public,
            "artifacts": artifacts,
            "source_database": source_database,
            "file_snapshots": {
                kind: {
                    "source_volume": binding["volumes"][kind],
                    "pre_tree_sha256": tree,
                    "archive_tree_sha256": tree,
                    "post_tree_sha256": tree,
                    "member_count": 1,
                    "expanded_bytes": expanded[kind],
                    "stable_attempt": 1,
                }
                for kind, tree in trees.items()
            },
            "redis_rollback_only": {
                "policy": "sealed-rollback-evidence-only",
                "source_volume": binding["volumes"]["redis"],
                "tree_sha256": hashlib.sha256(
                    f"redis-tree:{role}".encode()
                ).hexdigest(),
                "metadata_sha256": hashlib.sha256(
                    f"redis-metadata:{role}".encode()
                ).hexdigest(),
                "member_count": 1,
                "bytes": 10,
                "stable_attempt": 1,
                "archive_created": False,
                "restore": False,
            },
            "restore_drill": {
                "status": "passed",
                "postgres_image_reference": binding["images"][
                    "restore_postgres"
                ],
                "postgres_image_id": inventory["images"][
                    "restore_postgres"
                ]["image_id"],
                "postgres_runtime_uid": SOURCE.POSTGRES_RUNTIME_UID,
                "postgres_runtime_gid": SOURCE.POSTGRES_RUNTIME_GID,
                "scratch_postgres_system_id": "123456789012345",
                "single_transaction": True,
                "network_mode": "none",
                "pull_policy": "never",
                "source_or_current_mounted": False,
                "recovered_prior_residue": False,
                "scratch_resources_removed": True,
                "zero_residue": True,
            },
            "source_mutated": False,
            "current_mutated": False,
            "source_stopped_or_restarted": False,
            "redis_restored": False,
        }
        manifest_path = final / SOURCE.MANIFEST_FILE
        secure_file(manifest_path, canonical_json_bytes(document))
        self.sources[role] = document
        self.freeze_paths[role] = freeze_path
        self.source_paths[role] = manifest_path

    def _write_claim(self) -> None:
        role_generation = {
            role: self.receipt["readbacks"][role]["generation_sha256"]
            for role in NGINX.ROLE_ORDER
        }
        claim = {
            "schema": MODULE.LIVE_LEASE_CLAIM_SCHEMA,
            "status": "active",
            "owner_action": "capture-frozen-final-snapshots",
            "operation_id": self.controller["operation_id"],
            "release_sha": self.controller["release_sha"],
            "release_tree_sha": self.controller["release_tree_sha"],
            "aggregate_sha256": self.receipt["aggregate_sha256"],
            "claim_epoch": 1,
            "previous_claim_sha256": "0" * 64,
            "nonce": hashlib.sha256(b"lease-nonce").hexdigest(),
            "controller_pid": 4242,
            "controller_lock_path": os.fspath(
                self.control_root
                / self.controller["operation_id"]
                / "nginx-coordinator"
                / "coordinator.lock"
            ),
            "controller_authoritative": True,
            "remote_copy_authoritative": False,
            "automatic_expiry_allowed": False,
            "reconciliation_required_after_crash": True,
            "legacy_frozen_receipt_path": os.fspath(self.receipt_path),
            "legacy_frozen_receipt_sha256": self.receipt_sha256,
            "receipt_journal_sha256": self.receipt["journal_sha256"],
            "receipt_journal_sequence": self.receipt["evidence_count"],
            "receipt_journal_tail_sha256": self.receipt[
                "evidence_tail_sha256"
            ],
            "controller_journal_event_count": self.receipt["evidence_count"],
            "receipt_state": "legacy-frozen",
            "receipt_global_generation_sha256": self.receipt[
                "global_generation_sha256"
            ],
            "receipt_role_generation_sha256": role_generation,
            "receipt_role_bindings": self.receipt["role_bindings"],
            "receipt_readbacks": self.receipt["readbacks"],
        }
        payload = canonical_json_bytes(claim)
        self.claim_sha256 = hashlib.sha256(payload).hexdigest()
        self.claim_path = (
            self.control_root
            / self.controller["operation_id"]
            / "nginx-coordinator"
            / "live-leases"
            / "claims"
            / f"{self.claim_sha256}.json"
        )
        secure_file(self.claim_path, payload)

    def _webapp_restore_input_sha256(self) -> str:
        role = "webapp_fi"
        document = self.sources[role]
        binding, binding_sha256 = self._binding(role)
        freeze_sha256 = hashlib.sha256(
            self.freeze_paths[role].read_bytes()
        ).hexdigest()
        row = {
            "source_snapshot_manifest_sha256": hashlib.sha256(
                self.source_paths[role].read_bytes()
            ).hexdigest(),
            "source_snapshot_binding_sha256": binding_sha256,
            "freeze_evidence_sha256": freeze_sha256,
            "live_lease_claim_sha256": self.claim_sha256,
            "source_identity_sha256": document["source"][
                "identity_sha256"
            ],
            "artifacts": document["artifacts"],
            "source_database": document["source_database"],
        }
        self.assert_binding = binding
        return digest(row)

    def _write_ir_transport(self) -> None:
        ciphertext_sha256 = hashlib.sha256(
            b"age encrypted WebApp-FI frozen-final restore input"
        ).hexdigest()
        ciphertext_bytes = 8123
        bucket = "three-site-private"
        object_key = (
            f"production-shadow/{self.controller['operation_id']}/"
            "webapp-ir/frozen-final.age"
        )
        version_id = "arvan-version-0001"
        readback = {
            "schema": MODULE.IR_READBACK_SCHEMA,
            "status": "read-back-verified",
            "operation_id": self.controller["operation_id"],
            "release_sha": self.controller["release_sha"],
            "source_role": "webapp_fi",
            "target_role": "webapp_ir",
            "provider": "arvan-s3",
            "bucket": bucket,
            "object_key": object_key,
            "version_id": version_id,
            "ciphertext_sha256": ciphertext_sha256,
            "ciphertext_bytes": ciphertext_bytes,
            "exact_version_requested": True,
            "body_sha256": ciphertext_sha256,
            "body_bytes": ciphertext_bytes,
        }
        self.ir_readback_path = self.input_root / "ir-readback.json"
        secure_file(
            self.ir_readback_path,
            canonical_json_bytes(readback),
        )
        readback_sha256 = hashlib.sha256(
            self.ir_readback_path.read_bytes()
        ).hexdigest()
        transport = {
            "schema": MODULE.IR_TRANSPORT_SCHEMA,
            "status": "read-back-verified",
            "operation_id": self.controller["operation_id"],
            "release_sha": self.controller["release_sha"],
            "release_tree_sha": self.controller["release_tree_sha"],
            "controller_manifest_sha256": self.controller_sha256,
            "approval_sha256": self.controller["artifacts"][
                APPROVAL_HASH_FIELD
            ],
            "source_role": "webapp_fi",
            "target_role": "webapp_ir",
            "provider": "arvan-s3",
            "bucket": bucket,
            "private": True,
            "versioned": True,
            "encryption": "age",
            "recipient": "age1" + "q" * 58,
            "plaintext_restore_input_set_sha256": (
                self._webapp_restore_input_sha256()
            ),
            "ciphertext_sha256": ciphertext_sha256,
            "ciphertext_bytes": ciphertext_bytes,
            "object_key": object_key,
            "version_id": version_id,
            "readback_receipt_sha256": readback_sha256,
        }
        self.ir_transport_path = self.input_root / "ir-transport.json"
        secure_file(
            self.ir_transport_path,
            canonical_json_bytes(transport),
        )

    def inputs(self) -> dict[str, Path]:
        return {
            "controller_manifest": self.controller_path,
            "approval": self.approval_path,
            "approval_policy": self.policy_path,
            "bot_fi_source_manifest": self.source_paths["bot_fi"],
            "bot_fi_freeze_evidence": self.freeze_paths["bot_fi"],
            "webapp_fi_source_manifest": self.source_paths["webapp_fi"],
            "webapp_fi_freeze_evidence": self.freeze_paths["webapp_fi"],
            "legacy_frozen_receipt": self.receipt_path,
            "live_lease_claim": self.claim_path,
            "webapp_ir_transport": self.ir_transport_path,
            "webapp_ir_readback_receipt": self.ir_readback_path,
        }


class FrozenFinalRestoreSetTests(unittest.TestCase):
    def setUp(self) -> None:
        freeze_schema = mock.patch.object(
            SOURCE,
            "FREEZE_SCHEMA",
            MODULE.SOURCE_FREEZE_EVIDENCE_SCHEMA,
        )
        freeze_fields = mock.patch.object(
            SOURCE,
            "FREEZE_FIELDS",
            SOURCE.FREEZE_FIELDS | {"live_lease_claim_sha256"},
        )
        freeze_schema.start()
        freeze_fields.start()
        self.addCleanup(freeze_fields.stop)
        self.addCleanup(freeze_schema.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = FrozenFinalRestoreSetFixture(self.root)
        self.prefix = mock.patch.object(
            MODULE,
            "CONTROLLER_SECRET_PREFIX",
            self.fixture.control_root,
        )
        self.prefix.start()
        self.addCleanup(self.prefix.stop)

    def test_plan_binds_exact_global_closure_without_io_or_output(self) -> None:
        document = MODULE.build_restore_set(**self.fixture.inputs())
        result = MODULE.execute(
            **self.fixture.inputs(),
            output_root=self.fixture.output_root,
            apply=False,
            confirm=None,
        )

        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["output_created"])
        self.assertFalse(result["network_io"])
        self.assertFalse(result["object_storage_contacted"])
        self.assertFalse(result["runtime_mutated"])
        self.assertFalse(Path(result["output"]).exists())
        self.assertEqual(document["target_map"], MODULE.TARGET_MAP)
        self.assertEqual(
            document["target_map"]["webapp_ir"],
            {
                "source_role": "webapp_fi",
                "transport": "arvan-private-versioned-age",
            },
        )
        self.assertEqual(
            document["snapshot_authorization_claim"]["claim_sha256"],
            self.fixture.claim_sha256,
        )
        self.assertFalse(
            document["snapshot_authorization_claim"][
                "copied_material_authoritative"
            ]
        )
        self.assertTrue(
            document["snapshot_authorization_claim"][
                "fresh_live_authority_required_before_install_or_restore"
            ]
        )
        self.assertFalse(
            document["snapshot_authorization_claim"][
                "future_install_or_restore_authority_implied"
            ]
        )
        self.assertFalse(
            document["snapshot_authorization_claim"][
                "claim_liveness_asserted"
            ]
        )
        self.assertTrue(
            all(
                source["live_lease_claim_sha256"]
                == self.fixture.claim_sha256
                for source in document["sources"].values()
            )
        )
        self.assertEqual(
            document["nginx_freeze"]["state_receipt_sha256"],
            self.fixture.receipt_sha256,
        )
        self.assertEqual(
            document["sources"]["webapp_fi"]["restore_input_sha256"],
            document["webapp_ir_transport"][
                "plaintext_restore_input_set_sha256"
            ],
        )
        self.assertNotEqual(
            document["postgres_snapshot_set_sha256"],
            document["reviewed_file_snapshot_set_sha256"],
        )
        self.assertTrue(
            all(
                source["redis_restore_included"] is False
                for source in document["sources"].values()
            )
        )

    def test_apply_is_create_only_canonical_and_idempotent(self) -> None:
        plan = MODULE.execute(
            **self.fixture.inputs(),
            output_root=self.fixture.output_root,
            apply=False,
            confirm=None,
        )
        applied = MODULE.execute(
            **self.fixture.inputs(),
            output_root=self.fixture.output_root,
            apply=True,
            confirm=plan["required_confirmation"],
        )

        output = Path(applied["output"])
        self.assertEqual(applied["status"], "published")
        self.assertEqual(applied["publication"], "created")
        self.assertTrue(applied["output_created"])
        self.assertEqual(output.name, MODULE.OUTPUT_FILENAME)
        self.assertEqual(output.parent.name, applied["restore_set_sha256"])
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        payload = output.read_bytes()
        document = json.loads(payload)
        self.assertEqual(payload, canonical_json_bytes(document))
        self.assertEqual(hashlib.sha256(payload).hexdigest(), output.parent.name)
        files = [path for path in self.fixture.output_root.rglob("*") if path.is_file()]
        self.assertEqual(files, [output])

        repeated = MODULE.execute(
            **self.fixture.inputs(),
            output_root=self.fixture.output_root,
            apply=True,
            confirm=plan["required_confirmation"],
        )
        self.assertEqual(repeated["status"], "already-published")
        self.assertEqual(repeated["publication"], "reused")
        self.assertFalse(repeated["output_created"])
        self.assertEqual(output.read_bytes(), payload)

    def test_existing_different_destination_is_never_replaced(self) -> None:
        document = MODULE.build_restore_set(**self.fixture.inputs())
        output, _digest = MODULE.restore_set_path(
            self.fixture.output_root,
            document,
        )
        operation = MODULE._ensure_private_child(
            self.fixture.output_root,
            self.fixture.controller["operation_id"],
        )
        sets = MODULE._ensure_private_child(
            operation,
            "frozen-final-restore-sets",
        )
        MODULE._ensure_private_child(sets, output.parent.name)
        secure_file(output, b"foreign")

        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreSetError,
            "refusing to replace",
        ):
            MODULE.publish_restore_set(
                self.fixture.output_root,
                document,
            )
        self.assertEqual(output.read_bytes(), b"foreign")

    def test_digest_namespace_with_unexpected_entry_is_rejected(self) -> None:
        document = MODULE.build_restore_set(**self.fixture.inputs())
        output, _digest = MODULE.restore_set_path(
            self.fixture.output_root,
            document,
        )
        operation = MODULE._ensure_private_child(
            self.fixture.output_root,
            self.fixture.controller["operation_id"],
        )
        sets = MODULE._ensure_private_child(
            operation,
            "frozen-final-restore-sets",
        )
        namespace = MODULE._ensure_private_child(sets, output.parent.name)
        secure_file(namespace / "foreign.json", b"foreign")

        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreSetError,
            "unexpected entries",
        ):
            MODULE.publish_restore_set(
                self.fixture.output_root,
                document,
            )
        self.assertFalse(output.exists())

    def test_live_baseline_or_artifact_drift_fails_closed(self) -> None:
        manifest_path = self.fixture.source_paths["bot_fi"]
        original = manifest_path.read_bytes()
        document = json.loads(original)
        document["mode"] = "live-baseline"
        secure_file(manifest_path, canonical_json_bytes(document))
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreSetError,
            "frozen-final",
        ):
            MODULE.build_restore_set(**self.fixture.inputs())

        secure_file(manifest_path, original)
        database = manifest_path.parent / SOURCE.ARTIFACT_FILES[
            "database-backup"
        ]
        secure_file(database, b"drift")
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreSetError,
            "source closure is invalid",
        ):
            MODULE.build_restore_set(**self.fixture.inputs())

    def test_claim_and_ir_exact_version_drift_fail_closed(self) -> None:
        claim_original = self.fixture.claim_path.read_bytes()
        claim = json.loads(claim_original)
        claim["receipt_journal_sequence"] += 1
        secure_file(self.fixture.claim_path, canonical_json_bytes(claim))
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreSetError,
            "snapshot-authorization claim",
        ):
            MODULE.build_restore_set(**self.fixture.inputs())

        secure_file(self.fixture.claim_path, claim_original)
        readback = json.loads(self.fixture.ir_readback_path.read_bytes())
        readback["version_id"] = "different-version"
        secure_file(
            self.fixture.ir_readback_path,
            canonical_json_bytes(readback),
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreSetError,
            "transport binding differs|readback receipt differs",
        ):
            MODULE.build_restore_set(**self.fixture.inputs())

    def test_legacy_restore_owner_action_cannot_authorize_snapshot_set(
        self,
    ) -> None:
        claim = json.loads(self.fixture.claim_path.read_bytes())
        claim["owner_action"] = "restore-legacy-writers"
        secure_file(
            self.fixture.claim_path,
            canonical_json_bytes(claim),
        )

        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreSetError,
            "snapshot-authorization claim",
        ):
            MODULE.build_restore_set(**self.fixture.inputs())

    def test_source_freeze_claim_mismatch_fails_closed(self) -> None:
        freeze_path = self.fixture.freeze_paths["webapp_fi"]
        freeze = json.loads(freeze_path.read_bytes())
        freeze["live_lease_claim_sha256"] = "a" * 64
        secure_file(freeze_path, canonical_json_bytes(freeze))
        manifest_path = self.fixture.source_paths["webapp_fi"]
        manifest = json.loads(manifest_path.read_bytes())
        manifest["freeze_evidence_sha256"] = hashlib.sha256(
            freeze_path.read_bytes()
        ).hexdigest()
        secure_file(manifest_path, canonical_json_bytes(manifest))

        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreSetError,
            "snapshot-authorization claim differs",
        ):
            MODULE.build_restore_set(**self.fixture.inputs())

    def test_approval_bytes_are_verified_not_only_hash_shaped(self) -> None:
        token = json.loads(self.fixture.approval_path.read_bytes())
        token["signature"] = base64.b64encode(b"\x00" * 64).decode("ascii")
        secure_file(
            self.fixture.approval_path,
            canonical_json_bytes(token),
        )
        with self.assertRaisesRegex(
            MODULE.FrozenFinalRestoreSetError,
            "manifest or live approval is invalid",
        ):
            MODULE.build_restore_set(**self.fixture.inputs())


if __name__ == "__main__":
    unittest.main()
