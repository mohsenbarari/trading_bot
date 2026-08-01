from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stdout
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core.physical_postgres_deployment_scaffold import (
    ADAPTER_BINARY_NAMES,
    ADAPTER_CONTRACTS,
    ADAPTER_KINDS,
    ACK_MODE_BOUNDED_RPO_ARCHIVE,
    ACK_MODE_STRICT_REMOTE_DURABLE_REPLAY,
    PROFILE_BOUNDED_RPO_ARCHIVE,
    PROFILE_STRICT_ZERO_LOSS,
    AdapterInstallation,
    PhysicalPostgresDeploymentError,
    canonical_json_bytes,
    parse_physical_postgres_deployment_manifest,
    render_physical_postgres_deployment,
    validate_physical_postgres_deployment_manifest,
    verify_physical_postgres_adapter_installations,
)
from scripts import guard_physical_postgres_launch
from scripts.render_physical_postgres_deployment import (
    FilesystemAdapterInstallationInspector,
    PhysicalPostgresDeploymentCliError,
    load_templates,
    materialize_fresh_render,
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def adapter_document(kind: str, *, profile: str, route_hash: str) -> dict[str, object]:
    adapter_id = f"{kind.replace('_', '-')}-adapter-0001"
    result: dict[str, object] = {
        "adapter_id": adapter_id,
        "site": "webapp_fi" if kind in {
            "primary_term_guard",
            "wal_spool",
            "wal_uploader",
            "writer_ack",
        } else "webapp_ir",
        "contract": ADAPTER_CONTRACTS[kind],
        "binary_path": (
            "/opt/trading-bot/physical-postgres/adapters/"
            f"{adapter_id}/{ADAPTER_BINARY_NAMES[kind]}"
        ),
        "binary_sha256": digest(f"{kind}:binary"),
        "contract_sha256": digest(f"{kind}:contract"),
        "installation_attestation_sha256": digest(f"{kind}:attestation"),
        "route_binding_sha256": route_hash,
    }
    if kind == "writer_ack":
        result["writer_admission_integration_sha256"] = digest("writer-admission")
        if profile == PROFILE_STRICT_ZERO_LOSS:
            result["acknowledgement_mode"] = ACK_MODE_STRICT_REMOTE_DURABLE_REPLAY
            result["strict_remote_durable_replay_identity_sha256"] = digest("strict-ack")
        else:
            result["acknowledgement_mode"] = ACK_MODE_BOUNDED_RPO_ARCHIVE
            result["maximum_rpo_seconds"] = 30
    return result


def deployment_document(*, profile: str = PROFILE_BOUNDED_RPO_ARCHIVE) -> dict[str, object]:
    route_hash = digest("route-binding")
    return {
        "schema": "gold-trade-physical-postgres-deployment-manifest-v1",
        "mode": "default-off",
        "campaign_id": "physical-fi-ir-20260731",
        "release_sha": "a" * 40,
        "postgres_image": (
            "registry.example/postgres@sha256:"
            "fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786"
        ),
        "postgres_major": 15,
        "postgres_runtime_identity": {
            "image_digest": "sha256:fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786",
            "platform": "linux/amd64",
            "effective_uid": 999,
            "effective_gid": 999,
            "attestation_sha256": digest("postgres-image-runtime"),
        },
        "deployment_profile": profile,
        "baseline": {
            "base_generation_id": "fi-base-generation-0001",
            "timeline": 1,
            "consistent_wal_lsn": "0/16B6C50",
            "base_backup_object_key": (
                "physical-postgres/physical-fi-ir-20260731/"
                "fi-base-generation-0001/base.tar.age"
            ),
            "base_backup_object_version_id": "version-physical-base-0001",
            "base_backup_ciphertext_sha256": digest("base:ciphertext"),
            "base_backup_plaintext_sha256": digest("base:plaintext"),
        },
        "writer_term": {
            "holder_site": "webapp_fi",
            "writer_epoch": 41,
            "writer_lease_id": "writer-lease-00000041",
            "witness_transition_id": "witness-transition-00000041",
            "term_proof_sha256": digest("witness-term"),
        },
        "route": {
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "delivery_route": "private-versioned-object-storage-pull-ack-v1",
            "route_binding_sha256": route_hash,
            "direct_fi_to_ir_ssh": False,
            "direct_fi_to_ir_scp": False,
            "direct_fi_to_ir_postgres_control": False,
        },
        "primary": {
            "site": "webapp_fi",
            "postgres_data_volume": "physical_fi_postgres_data",
            "postgres_socket_volume": "physical_fi_postgres_socket",
            "wal_spool_volume": "physical_fi_wal_spool",
            "adapter_state_volume": "physical_fi_adapter_state",
            "runtime_network_name": "physical_fi_runtime",
            "local_base_backup": {
                "transport": "unix-socket-only",
                "socket_directory": "/var/run/postgresql",
                "port": 5432,
                "replication_role": "physical_backup",
                "peer_os_users": ["postgres"],
                "max_wal_senders": 1,
                "tcp_hba": "reject",
                "helper_execution": "digest-pinned-image-attested-container-v1",
            },
        },
        "standby": {
            "site": "webapp_ir",
            "postgres_data_volume": "physical_ir_postgres_data",
            "restore_spool_volume": "physical_ir_restore_spool",
            "receiver_state_volume": "physical_ir_receiver_state",
            "runtime_network_name": "physical_ir_runtime",
        },
        "adapters": {
            kind: adapter_document(kind, profile=profile, route_hash=route_hash)
            for kind in ADAPTER_KINDS
        },
    }


class StaticAdapterInspector:
    def __init__(self, *, absent_kind: str | None = None, corrupt_kind: str | None = None):
        self.absent_kind = absent_kind
        self.corrupt_kind = corrupt_kind

    def inspect(self, *, adapter):
        if adapter.kind == self.absent_kind:
            raise FileNotFoundError(adapter.binary_path)
        return AdapterInstallation(
            binary_path=adapter.binary_path,
            binary_sha256=(digest("wrong-binary") if adapter.kind == self.corrupt_kind else adapter.binary_sha256),
            installation_attestation_sha256=adapter.installation_attestation_sha256,
            owner_uid=0,
            mode=0o755,
            regular_file=True,
            ancestors_root_controlled=True,
        )


class PhysicalPostgresDeploymentScaffoldTests(unittest.TestCase):
    def validated(self, *, profile: str = PROFILE_BOUNDED_RPO_ARCHIVE):
        return validate_physical_postgres_deployment_manifest(
            deployment_document(profile=profile)
        )

    def verified(self, manifest, **inspector_kwargs):
        return verify_physical_postgres_adapter_installations(
            manifest, inspector=StaticAdapterInspector(**inspector_kwargs)
        )

    def render(self, manifest):
        return render_physical_postgres_deployment(
            manifest,
            verified_adapters=self.verified(manifest),
            templates=load_templates(),
        )

    def test_bounded_profile_renders_exact_default_off_primary_and_standby(self) -> None:
        manifest = self.validated()
        rendered = self.render(manifest)
        primary = rendered.file("primary/postgresql.conf").decode("utf-8")
        primary_hba = rendered.file("primary/pg_hba.conf").decode("utf-8")
        primary_ident = rendered.file("primary/pg_ident.conf").decode("utf-8")
        standby = rendered.file("standby/postgresql.conf").decode("utf-8")
        standby_hba = rendered.file("standby/pg_hba.conf").decode("utf-8")
        primary_compose = rendered.file("primary/docker-compose.yml").decode("utf-8")
        standby_compose = rendered.file("standby/docker-compose.yml").decode("utf-8")

        self.assertIn("wal_level = replica", primary)
        self.assertIn("listen_addresses = ''", primary)
        self.assertIn("unix_socket_directories = '/var/run/postgresql'", primary)
        self.assertIn("unix_socket_group = 'postgres'", primary)
        self.assertIn("unix_socket_permissions = 0770", primary)
        self.assertIn("archive_mode = on", primary)
        self.assertIn("max_wal_senders = 1", primary)
        self.assertIn("max_replication_slots = 0", primary)
        self.assertIn("synchronous_standby_names = ''", primary)
        self.assertIn("local replication physical_backup peer map=physical_base_backup_peer", " ".join(primary_hba.split()))
        self.assertIn("host all all 0.0.0.0/0 reject", " ".join(primary_hba.split()))
        self.assertIn("host all all ::0/0 reject", " ".join(primary_hba.split()))
        self.assertIn("physical_base_backup_peer postgres physical_backup", " ".join(primary_ident.split()))
        self.assertIn("archive_mode = always", standby)
        self.assertIn("hot_standby = on", standby)
        self.assertIn("restore_command = 'exec ", standby)
        self.assertIn("reverse-wal-spool", standby)
        self.assertIn("primary_conninfo = ''", standby)
        self.assertIn("primary_slot_name = ''", standby)
        self.assertIn("synchronous_standby_names = ''", standby)
        self.assertIn("listen_addresses = ''", standby)
        self.assertIn("unix_socket_directories = '/var/run/postgresql'", standby)
        self.assertIn("hba_file = '/etc/trading-bot/physical-postgres/standby/pg_hba.conf'", standby)
        self.assertIn("max_wal_senders = 0", standby)
        self.assertIn("max_replication_slots = 0", standby)
        self.assertIn("local all postgres peer", " ".join(standby_hba.split()))
        self.assertIn("local replication all reject", " ".join(standby_hba.split()))
        self.assertIn("host replication all 0.0.0.0/0 reject", " ".join(standby_hba.split()))
        self.assertIn("host all all 0.0.0.0/0 reject", " ".join(standby_hba.split()))
        self.assertIn("host replication all ::0/0 reject", " ".join(standby_hba.split()))
        self.assertIn("host all all ::0/0 reject", " ".join(standby_hba.split()))
        self.assertIn("profiles: [\"physical-postgres-primary\"]", primary_compose)
        self.assertIn("profiles: [\"physical-postgres-standby\"]", standby_compose)
        self.assertIn("source: physical_fi_postgres_socket", primary_compose)
        self.assertIn("target: /var/run/postgresql", primary_compose)
        self.assertIn("physical_fi_postgres_socket:\n    external: true", primary_compose)
        self.assertIn(
            "source: /etc/trading-bot/physical-postgres/rendered/standby\n"
            "        target: /etc/trading-bot/physical-postgres/standby\n"
            "        read_only: true",
            standby_compose,
        )
        self.assertNotIn("ports:", primary_compose)
        self.assertNotIn("ports:", standby_compose)

        binding_values = (
            manifest.release_sha,
            manifest.baseline.base_generation_id,
            manifest.writer_term_sha256,
            manifest.route.route_binding_sha256,
        )
        for text in (primary, standby, primary_compose, standby_compose):
            for value in binding_values:
                self.assertIn(value, text)
        lock = json.loads(rendered.file("manifest-lock.json"))
        self.assertEqual(lock["status"], "default-off-not-launch-authorized")
        self.assertTrue(lock["not_a_live_remote_ack_proof"])
        self.assertTrue(lock["not_a_launch_authorization"])
        self.assertEqual(
            "physical_fi_postgres_socket",
            lock["primary"]["postgres_socket_volume"],
        )
        self.assertEqual(
            1,
            lock["primary"]["local_base_backup"]["max_wal_senders"],
        )
        local_preflight = json.loads(
            rendered.file("primary/local-base-backup-auth-preflight.json")
        )
        self.assertEqual("default-off-not-launch-authorized", local_preflight["status"])
        self.assertTrue(local_preflight["not_a_role_creation_authorization"])
        self.assertEqual(
            "physical_backup", local_preflight["required_role_attributes"]["role"]
        )
        self.assertFalse(local_preflight["required_role_attributes"]["superuser"])
        self.assertEqual(
            json.loads(rendered.file("primary/writer-ack.json"))["adapter"]["acknowledgement_mode"],
            ACK_MODE_BOUNDED_RPO_ARCHIVE,
        )
        self.assertEqual(rendered.file("standby/recovery.signal"), b"")
        for _, payload in rendered.files:
            self.assertNotIn(b"@@", payload)
        for relative_path in (
            "primary/term-guard.json",
            "primary/wal-spool.json",
            "primary/wal-uploader.json",
            "primary/writer-ack.json",
            "standby/bootstrap.json",
            "standby/pull-agent.json",
            "standby/reverse-wal-spool.json",
            "standby/reverse-wal-uploader.json",
        ):
            descriptor = json.loads(rendered.file(relative_path))
            self.assertEqual(descriptor["release_sha"], manifest.release_sha)
            self.assertEqual(
                descriptor["baseline"]["base_generation_id"],
                manifest.baseline.base_generation_id,
            )
            self.assertEqual(
                descriptor["writer_term_sha256"], manifest.writer_term_sha256
            )
            self.assertEqual(
                descriptor["route"]["route_binding_sha256"],
                manifest.route.route_binding_sha256,
            )

    def test_actual_adapter_absence_or_hash_mismatch_fails_closed(self) -> None:
        manifest = self.validated()
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "wal_uploader adapter"):
            self.verified(manifest, absent_kind="wal_uploader")
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "standby_pull adapter"):
            self.verified(manifest, corrupt_kind="standby_pull")

    def test_manifest_requires_all_adapters_and_distinct_volumes(self) -> None:
        missing = deployment_document()
        del missing["adapters"]["wal_uploader"]
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "every required adapter"):
            validate_physical_postgres_deployment_manifest(missing)

        colliding = deployment_document()
        colliding["standby"]["postgres_data_volume"] = colliding["primary"]["postgres_data_volume"]
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "volumes must be distinct"):
            validate_physical_postgres_deployment_manifest(colliding)

        socket_collision = deployment_document()
        socket_collision["primary"]["postgres_socket_volume"] = socket_collision["primary"]["wal_spool_volume"]
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "volumes must be distinct"):
            validate_physical_postgres_deployment_manifest(socket_collision)

        missing_socket = deployment_document()
        del missing_socket["primary"]["postgres_socket_volume"]
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "primary fields"):
            validate_physical_postgres_deployment_manifest(missing_socket)

        zero_wal_sender = deployment_document()
        zero_wal_sender["primary"]["local_base_backup"]["max_wal_senders"] = 0
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "socket-only helper policy"):
            validate_physical_postgres_deployment_manifest(zero_wal_sender)

    def test_primary_socket_substrate_and_no_host_ports_are_renderer_invariants(self) -> None:
        manifest = self.validated()
        templates = load_templates()
        templates["docker-compose.primary.yml.template"] = templates[
            "docker-compose.primary.yml.template"
        ].replace(
            "      - type: volume\n        source: @@PRIMARY_SOCKET_VOLUME@@\n        target: /var/run/postgresql\n",
            "",
        )
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "socket volume"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["primary-postgresql.conf.template"] = templates[
            "primary-postgresql.conf.template"
        ].replace("unix_socket_directories = '/var/run/postgresql'\n", "")
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "socket-only"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["primary-postgresql.conf.template"] = templates[
            "primary-postgresql.conf.template"
        ].replace("max_wal_senders = @@LOCAL_BASE_BACKUP_MAX_WAL_SENDERS@@", "max_wal_senders = 0")
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "socket-only"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["primary-pg_hba.conf.template"] = templates[
            "primary-pg_hba.conf.template"
        ].replace(
            "local   replication     @@LOCAL_BASE_BACKUP_REPLICATION_ROLE@@     peer map=@@LOCAL_BASE_BACKUP_PEER_MAP@@\n",
            "",
        )
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "HBA"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["primary-pg_hba.conf.template"] = templates[
            "primary-pg_hba.conf.template"
        ].replace("0.0.0.0/0               reject", "0.0.0.0/0               trust")
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "HBA"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["primary-postgresql.conf.template"] = templates[
            "primary-postgresql.conf.template"
        ].replace("listen_addresses = ''", "listen_addresses = '127.0.0.1'")
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "socket-only"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["docker-compose.primary.yml.template"] += "    ports: [\"5432:5432\"]\n"
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "TCP or host network"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["docker-compose.primary.yml.template"] += "    expose: [\"5432\"]\n"
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "TCP or host network"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["docker-compose.standby.yml.template"] += "    ports: [\"5432:5432\"]\n"
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "TCP ports or host"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

    def test_standby_socket_hba_and_rendered_mount_are_renderer_invariants(self) -> None:
        manifest = self.validated()

        templates = load_templates()
        templates["standby-postgresql.conf.template"] = templates[
            "standby-postgresql.conf.template"
        ].replace("listen_addresses = ''", "listen_addresses = '*'")
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "standby PostgreSQL template"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["standby-postgresql.conf.template"] = templates[
            "standby-postgresql.conf.template"
        ].replace("primary_conninfo = ''", "primary_conninfo = 'host=fi.example.invalid'")
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "standby PostgreSQL template"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["standby-pg_hba.conf.template"] = templates[
            "standby-pg_hba.conf.template"
        ].replace("0.0.0.0/0               reject", "0.0.0.0/0               trust", 1)
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "standby HBA"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        templates["docker-compose.standby.yml.template"] = templates[
            "docker-compose.standby.yml.template"
        ].replace(
            "      - type: bind\n"
            "        source: /etc/trading-bot/physical-postgres/rendered/standby\n"
            "        target: /etc/trading-bot/physical-postgres/standby\n"
            "        read_only: true\n"
            "        bind:\n"
            "          create_host_path: false\n",
            "",
        )
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "rendered HBA mount"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

        templates = load_templates()
        del templates["standby-pg_hba.conf.template"]
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "template set"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

    def test_archive_only_ack_cannot_claim_strict_profile(self) -> None:
        document = deployment_document(profile=PROFILE_STRICT_ZERO_LOSS)
        ack = document["adapters"]["writer_ack"]
        ack.pop("strict_remote_durable_replay_identity_sha256")
        ack["acknowledgement_mode"] = ACK_MODE_BOUNDED_RPO_ARCHIVE
        ack["maximum_rpo_seconds"] = 30
        with self.assertRaises(PhysicalPostgresDeploymentError):
            validate_physical_postgres_deployment_manifest(document)

    def test_strict_profile_is_unrenderable_without_a_reviewed_object_storage_durable_replay_runtime(self) -> None:
        manifest = self.validated(profile=PROFILE_STRICT_ZERO_LOSS)
        verified = self.verified(manifest)
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "strict_zero_loss cannot render"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=verified,
                templates=load_templates(),
            )

    def test_direct_fi_to_ir_control_and_template_gaps_are_rejected(self) -> None:
        document = deployment_document()
        document["route"]["direct_fi_to_ir_postgres_control"] = True
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "direct FI-to-IR"):
            validate_physical_postgres_deployment_manifest(document)

        manifest = self.validated()
        templates = load_templates()
        templates["standby-postgresql.conf.template"] += "unknown = '@@UNKNOWN@@'\n"
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "unknown token"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )
        templates = load_templates()
        del templates["docker-compose.standby.yml.template"]
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "template set"):
            render_physical_postgres_deployment(
                manifest,
                verified_adapters=self.verified(manifest),
                templates=templates,
            )

    def test_canonical_parser_rejects_duplicate_keys_and_noncanonical_input(self) -> None:
        document = deployment_document()
        canonical = canonical_json_bytes(document) + b"\n"
        self.assertEqual(parse_physical_postgres_deployment_manifest(canonical), document)
        self.assertRaises(
            PhysicalPostgresDeploymentError,
            parse_physical_postgres_deployment_manifest,
            canonical[:-1],
        )
        duplicate = b'{"schema":"x","schema":"x"}\n'
        with self.assertRaisesRegex(PhysicalPostgresDeploymentError, "duplicate"):
            parse_physical_postgres_deployment_manifest(duplicate)

    def test_launch_guard_never_launches_and_returns_blocked(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = guard_physical_postgres_launch.main()
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "blocked")
        self.assertIn("execution coordinator", output.getvalue())

    @unittest.skipUnless(os.geteuid() == 0, "root-only inspector policy test")
    def test_filesystem_inspector_rejects_unsafe_binary_and_attestation_modes(self) -> None:
        inspector = FilesystemAdapterInstallationInspector()
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            binary = root / "adapter"
            attestation = root / "attestation.json"
            binary.write_bytes(b"adapter")
            attestation.write_bytes(b"attestation")
            os.chmod(binary, 0o755)
            os.chmod(attestation, 0o600)
            adapter = SimpleNamespace(
                binary_path=str(binary), attestation_path=str(attestation)
            )
            # ``/tmp`` is intentionally not a production-safe ancestor.  The
            # mode checks below are isolated with the ancestor policy mocked;
            # the production inspector always runs both checks.
            with patch(
                "scripts.render_physical_postgres_deployment._require_root_controlled_ancestors"
            ):
                original_lstat = Path.lstat

                def nonroot_binary_lstat(path: Path):
                    metadata = original_lstat(path)
                    if path == binary:
                        return SimpleNamespace(
                            st_mode=metadata.st_mode,
                            st_uid=65534,
                            st_nlink=metadata.st_nlink,
                        )
                    return metadata

                with patch.object(Path, "lstat", autospec=True, side_effect=nonroot_binary_lstat):
                    with self.assertRaisesRegex(PhysicalPostgresDeploymentCliError, "mode-0755"):
                        inspector.inspect(adapter=adapter)
                os.chmod(binary, 0o750)
                with self.assertRaisesRegex(PhysicalPostgresDeploymentCliError, "mode-0755"):
                    inspector.inspect(adapter=adapter)
                os.chmod(binary, 0o755)
                os.chmod(attestation, 0o644)
                with self.assertRaisesRegex(PhysicalPostgresDeploymentCliError, "mode-0600"):
                    inspector.inspect(adapter=adapter)
                os.chmod(attestation, 0o600)
                linked_attestation = root / "attestation-link.json"
                linked_attestation.symlink_to(attestation)
                linked_adapter = SimpleNamespace(
                    binary_path=str(binary), attestation_path=str(linked_attestation)
                )
                with self.assertRaisesRegex(PhysicalPostgresDeploymentCliError, "mode-0600"):
                    inspector.inspect(adapter=linked_adapter)

    @unittest.skipUnless(os.geteuid() == 0, "root-only render-root policy test")
    def test_materialize_refuses_a_nonempty_render_root(self) -> None:
        manifest = self.validated()
        rendered = self.render(manifest)
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            os.chmod(root, 0o700)
            (root / "preexisting").write_text("must not be overwritten", encoding="ascii")
            with patch(
                "scripts.render_physical_postgres_deployment._require_root_controlled_ancestors"
            ):
                with self.assertRaisesRegex(PhysicalPostgresDeploymentCliError, "must be empty"):
                    materialize_fresh_render(rendered, root=root)

    @unittest.skipUnless(os.geteuid() == 0, "root-only render-root policy test")
    def test_materialize_assigns_only_the_attested_postgres_group_read_access(self) -> None:
        """The image UID/GID is a manifest fact, not a source-code default."""

        manifest = self.validated()
        rendered = self.render(manifest)
        postgres_gid = manifest.postgres_runtime_identity.effective_gid
        chown_calls: list[tuple[Path, int, int]] = []
        fchown_calls: list[tuple[int, int, int]] = []
        assigned_directories: dict[Path, int] = {}
        original_lstat = Path.lstat

        def fake_chown(path, uid, gid):
            resolved = Path(path)
            chown_calls.append((resolved, uid, gid))
            assigned_directories[resolved] = gid

        def fake_fchown(descriptor, uid, gid):
            fchown_calls.append((descriptor, uid, gid))

        def fake_lstat(path: Path):
            metadata = original_lstat(path)
            assigned_gid = assigned_directories.get(path)
            if assigned_gid is None:
                return metadata
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_uid=0,
                st_gid=assigned_gid,
                st_nlink=metadata.st_nlink,
                st_size=metadata.st_size,
            )

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            os.chmod(root, 0o700)
            with patch(
                "scripts.render_physical_postgres_deployment._require_root_controlled_ancestors"
            ), patch(
                "scripts.render_physical_postgres_deployment.os.chown",
                side_effect=fake_chown,
            ), patch(
                "scripts.render_physical_postgres_deployment.os.fchown",
                side_effect=fake_fchown,
            ), patch.object(Path, "lstat", autospec=True, side_effect=fake_lstat):
                materialize_fresh_render(rendered, root=root)

            self.assertEqual((root, 0, postgres_gid), chown_calls[0])
            self.assertEqual(
                {(root / "primary", 0, postgres_gid), (root / "standby", 0, postgres_gid)},
                set(chown_calls[1:]),
            )
            self.assertEqual(len(rendered.files), len(fchown_calls))
            self.assertTrue(
                all(uid == 0 and gid == postgres_gid for _, uid, gid in fchown_calls)
            )
            self.assertEqual(
                rendered.file("primary/postgresql.conf"),
                (root / "primary" / "postgresql.conf").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
