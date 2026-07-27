from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
import yaml

from scripts import produce_production_shadow_prepare_material as MODULE
from scripts import wa_ir_production_operation as WA_CONSUMER
from scripts.render_three_site_production_shadow_role_compose import (
    parse_env_values,
)
from scripts.verify_three_site_production_shadow_compose import (
    _required_values,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_COMPOSE = (
    REPO_ROOT / "deploy/production/docker-compose.three-site-shadow.yml"
)
OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def secure_file(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def operation_ca() -> bytes:
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(b"\x11" * 32)
    name = x509.Name(
        [
            x509.NameAttribute(
                x509.NameOID.COMMON_NAME,
                f"trading-bot-production-shadow-dr-{OPERATION_ID}",
            )
        ]
    )
    current = datetime.now(timezone.utc).replace(microsecond=0)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(1)
        .not_valid_before(current.replace(microsecond=0))
        .not_valid_after(datetime(2099, 1, 1, tzinfo=timezone.utc))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=1),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, algorithm=None)
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


def tar_members(payload: bytes) -> dict[str, tuple[tarfile.TarInfo, bytes]]:
    result: dict[str, tuple[tarfile.TarInfo, bytes]] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive:
            source = archive.extractfile(member)
            result[member.name] = (
                member,
                b"" if source is None else source.read(),
            )
    return result


class PrepareFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.required_uid = os.geteuid()
        self.input = root / "input"
        self.output = root / "output"
        self.output_second = root / "output-second"
        for directory in (self.input, self.output, self.output_second):
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)

        self.compose = self.input / "canonical-compose.yml"
        self.compose_bytes = CANONICAL_COMPOSE.read_bytes()
        secure_file(self.compose, self.compose_bytes, mode=0o644)
        self.compose_sha256 = hashlib.sha256(
            self.compose_bytes
        ).hexdigest()

        self.ca = operation_ca()
        self.ca_path = self.input / "ca.crt"
        secure_file(self.ca_path, self.ca)
        certificate = x509.load_pem_x509_certificate(self.ca)
        generated_at = datetime.now(timezone.utc).replace(microsecond=0)
        self.ca_attestation_document = {
            "schema": MODULE.DR_CA_ATTESTATION_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "ca_sha256": hashlib.sha256(self.ca).hexdigest(),
            "ca_subject": certificate.subject.rfc4514_string(),
            "ca_serial_hex": format(certificate.serial_number, "x"),
            "not_before": MODULE._certificate_timestamp(
                certificate.not_valid_before
            ),
            "not_after": MODULE._certificate_timestamp(
                certificate.not_valid_after
            ),
            "generated_at": generated_at.isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
            "private_key_mode": "0600",
            "private_key_retained_on_controller": True,
            "old_tls_material_reused": False,
        }
        self.ca_attestation_bytes = canonical_json(
            self.ca_attestation_document
        )
        self.ca_attestation_path = self.input / "dr-ca-attestation.json"
        secure_file(
            self.ca_attestation_path,
            self.ca_attestation_bytes,
        )
        self.ca_attested_epoch = int(generated_at.timestamp())

        self.witness_document = {
            "schema": MODULE.WITNESS_PUBLIC_INPUT_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": RELEASE_TREE_SHA,
            "release_manifest_sha256": "1" * 64,
            "health_attestation_sha256": "2" * 64,
            "health_attested_at_epoch": int(
                datetime.now(timezone.utc).timestamp()
            ),
            "ca_sha256": "3" * 64,
            "server_cert_sha256": "4" * 64,
            "native_release_reused": True,
            "current_mutated": False,
            "service_mutated": False,
            "legacy_secret_material_copied": False,
        }
        self.witness_bytes = canonical_json(self.witness_document)
        self.witness_path = self.input / "witness-public.json"
        secure_file(self.witness_path, self.witness_bytes)

        image_chars = {
            "bot_fi": ("1", "2", "3", "4"),
            "webapp_fi": ("5", "6", "7", "8"),
            "webapp_ir": ("9", "a", "b", "c"),
        }
        self.runtime_ids = {
            role: {
                kind: f"sha256:{character * 64}"
                for kind, character in zip(MODULE.IMAGE_KINDS, characters)
            }
            for role, characters in image_chars.items()
        }
        self.stage_document = {
            "schema": MODULE.STAGE_BINDINGS_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "roles": {
                role: {
                    "stage_operation_manifest_sha256": (
                        f"{index + 5:x}" * 64
                    )[:64],
                    "stage_attestation_sha256": (
                        hashlib.sha256(self.witness_bytes).hexdigest()
                        if role == "witness"
                        else (f"{index + 9:x}" * 64)[:64]
                    ),
                    "runtime_image_ids": (
                        self.runtime_ids[role]
                        if role in MODULE.DOCKER_ROLES
                        else {}
                    ),
                }
                for index, role in enumerate(MODULE.ALL_ROLES)
            },
        }
        self.stage_path = self.input / "stage-bindings.json"
        secure_file(self.stage_path, canonical_json(self.stage_document))

        compose_text = self.compose_bytes.decode("utf-8")
        values = {
            name: f"value-{name.lower().replace('_', '-')}"
            for name in _required_values(compose_text)
        }
        project = f"tb3p-{OPERATION_ID.replace('-', '')}"
        project_root = (
            f"/srv/trading-bot-three-site-production-shadow/{OPERATION_ID}"
        )
        values.update(
            {
                "PRODUCTION_SHADOW_OPERATION_ID": OPERATION_ID,
                "PRODUCTION_SHADOW_PROJECT": project,
                "PRODUCTION_SHADOW_CGROUP_PARENT": project,
                "PRODUCTION_SHADOW_PROJECT_ROOT": project_root,
                "PRODUCTION_SHADOW_RELEASE_ROOT": (
                    f"{project_root}/releases/{RELEASE_SHA}"
                ),
                "PRODUCTION_SHADOW_DATA_ROOT": (
                    "/srv/trading-bot-three-site-production-shadow-data/"
                    f"{OPERATION_ID}"
                ),
                "PRODUCTION_SHADOW_SECRET_ROOT": (
                    "/root/secure-envs/trading-bot/"
                    f"three-site-production-shadow/{OPERATION_ID}"
                ),
                "PRODUCTION_SHADOW_RELEASE_SHA": RELEASE_SHA,
                "PRODUCTION_SHADOW_DR_CA_SHA256": hashlib.sha256(
                    self.ca
                ).hexdigest(),
                "PRODUCTION_SHADOW_DR_TLS_ATTESTATION_SHA256": (
                    hashlib.sha256(
                        self.ca_attestation_bytes
                    ).hexdigest()
                ),
                "PRODUCTION_SHADOW_DR_TLS_ATTESTED_AT_EPOCH": str(
                    self.ca_attested_epoch
                ),
                "PRODUCTION_SHADOW_APP_IMAGE_ID": "sha256:" + "e" * 64,
                "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": "sha256:" + "f" * 64,
                "PRODUCTION_SHADOW_REDIS_IMAGE_ID": "sha256:" + "0" * 64,
                "PRODUCTION_SHADOW_NGINX_IMAGE_ID": "sha256:" + "1" * 64,
                "BOT_TOKEN": "must-not-leak",
                "ARVAN_S3_SECRET_ACCESS_KEY": "must-not-leak-either",
            }
        )
        self.env_path = self.input / "canonical.env"
        secure_file(
            self.env_path,
            (
                "\n".join(
                    f"{name}={value}"
                    for name, value in sorted(values.items())
                )
                + "\n"
            ).encode("ascii"),
        )

    def produce(
        self,
        *,
        output: Path | None = None,
        metadata: bool = True,
    ) -> dict[str, object]:
        selected = output or self.output
        return MODULE.produce_prepare_materials(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            canonical_compose=self.compose,
            expected_compose_sha256=self.compose_sha256,
            environment_source=self.env_path,
            ca_certificate=self.ca_path,
            dr_tls_attestation=self.ca_attestation_path,
            stage_bindings=self.stage_path,
            witness_public_input=self.witness_path,
            output_directory=selected,
            metadata_output=(
                selected / "prepare-materials.json"
                if metadata
                else None
            ),
            required_uid=self.required_uid,
        )


class ProductionShadowPrepareMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.fixture = PrepareFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_exact_controller_compatible_prepare_archives(self) -> None:
        result = self.fixture.produce()

        self.assertEqual(result["schema"], MODULE.SET_SCHEMA)
        self.assertEqual(result["operation_id"], OPERATION_ID)
        self.assertFalse(result["activation_secrets_included"])
        self.assertFalse(result["precommit_manifest_bound"])
        self.assertEqual(
            result["dr_tls_attestation_sha256"],
            hashlib.sha256(
                self.fixture.ca_attestation_bytes
            ).hexdigest(),
        )
        self.assertEqual(
            result["dr_tls_attested_at_epoch"],
            self.fixture.ca_attested_epoch,
        )
        self.assertEqual(set(result["roles"]), set(MODULE.ALL_ROLES))
        controller = result["controller_bindings"]
        self.assertEqual(
            set(controller["role_materials"]),
            set(MODULE.ALL_ROLES),
        )
        self.assertEqual(
            set(controller["role_runtime_image_ids"]),
            set(MODULE.DOCKER_ROLES),
        )

        digests: set[str] = set()
        for role in MODULE.DOCKER_ROLES:
            with self.subTest(role=role):
                metadata = result["roles"][role]
                archive = (
                    self.fixture.output / metadata["filename"]
                ).read_bytes()
                self.assertEqual(
                    hashlib.sha256(archive).hexdigest(),
                    metadata["sha256"],
                )
                self.assertEqual(len(archive), metadata["bytes"])
                self.assertEqual(
                    metadata["format"],
                    "production-shadow-role-material-tar",
                )
                self.assertEqual(
                    controller["role_materials"][role],
                    {
                        "sha256": metadata["sha256"],
                        "bytes": metadata["bytes"],
                        "format": metadata["format"],
                        "transport": metadata["transport"],
                    },
                )
                members = tar_members(archive)
                self.assertEqual(
                    set(members),
                    {
                        "final-prepare-manifest.json",
                        "role-compose.yml",
                        "runtime.env.role",
                        "ca.crt",
                    },
                )
                for member, _payload in members.values():
                    self.assertTrue(member.isreg())
                    self.assertEqual(member.uid, 0)
                    self.assertEqual(member.gid, 0)
                    self.assertEqual(stat.S_IMODE(member.mode), 0o600)
                    self.assertEqual(member.mtime, 0)
                manifest = json.loads(
                    members["final-prepare-manifest.json"][1]
                )
                self.assertEqual(set(manifest), MODULE.FINAL_PREPARE_FIELDS)
                self.assertEqual(manifest["operation_id"], OPERATION_ID)
                self.assertEqual(manifest["release_sha"], RELEASE_SHA)
                self.assertEqual(manifest["role"], role)
                self.assertEqual(
                    manifest["schema"],
                    (
                        MODULE.WA_IR_FINAL_PREPARE_SCHEMA
                        if role == "webapp_ir"
                        else MODULE.FI_FINAL_PREPARE_SCHEMA
                    ),
                )
                self.assertEqual(
                    manifest["operation_manifest_sha256"],
                    self.fixture.stage_document["roles"][role][
                        "stage_operation_manifest_sha256"
                    ],
                )
                self.assertNotIn("role_material_sha256", manifest)
                self.assertNotIn("precommit_manifest_sha256", manifest)
                self.assertEqual(
                    manifest["runtime_image_ids"],
                    self.fixture.runtime_ids[role],
                )
                destinations = {
                    entry["destination"] for entry in manifest["entries"]
                }
                role_path = role.replace("_", "-")
                self.assertEqual(
                    destinations,
                    {
                        f"rendered/{role_path}/docker-compose.yml",
                        f"secrets/{role_path}/runtime.env.role",
                        "secrets/tls/ca.crt",
                    },
                )
                environment = parse_env_values(
                    members["runtime.env.role"][1].decode("ascii")
                )
                self.assertEqual(
                    {
                        kind: environment[env_name]
                        for kind, env_name in MODULE.IMAGE_ENV_BY_KIND.items()
                    },
                    self.fixture.runtime_ids[role],
                )
                self.assertNotIn("BOT_TOKEN", environment)
                self.assertNotIn(
                    "ARVAN_S3_SECRET_ACCESS_KEY",
                    environment,
                )
                self.assertNotIn(b"must-not-leak", archive)
                self.assertEqual(
                    members["ca.crt"][1],
                    self.fixture.ca,
                )
                rendered = yaml.safe_load(
                    members["role-compose.yml"][1]
                )
                self.assertEqual(
                    rendered["x-production-shadow-runtime-image-ids"],
                    MODULE.RUNTIME_IMAGE_COMPOSE_EXTENSION,
                )
                self.assertTrue(rendered["services"])
                self.assertTrue(
                    all(
                        "prepare" in " ".join(service["profiles"])
                        or "restore" in " ".join(service["profiles"])
                        for service in rendered["services"].values()
                    )
                )
                self.assertNotIn("ports", json.dumps(rendered))
                digests.add(metadata["sha256"])

        witness_metadata = result["roles"]["witness"]
        witness_archive = (
            self.fixture.output / witness_metadata["filename"]
        ).read_bytes()
        witness_members = tar_members(witness_archive)
        self.assertEqual(
            set(witness_members),
            {
                MODULE.WITNESS_MANIFEST_NAME,
                MODULE.WITNESS_ATTESTATION_NAME,
            },
        )
        witness_manifest = json.loads(
            witness_members[MODULE.WITNESS_MANIFEST_NAME][1]
        )
        self.assertEqual(
            witness_manifest["schema"],
            MODULE.WITNESS_PREPARE_SCHEMA,
        )
        self.assertEqual(witness_manifest["runtime_image_ids"], {})
        self.assertEqual(witness_manifest["required_env_keys"], [])
        self.assertEqual(
            witness_members[MODULE.WITNESS_ATTESTATION_NAME][1],
            self.fixture.witness_bytes,
        )
        self.assertNotIn(b"PRIVATE KEY", witness_archive)
        self.assertNotIn(b"issuer", witness_archive.lower())
        self.assertNotIn(b"token", witness_archive.lower())
        digests.add(witness_metadata["sha256"])
        self.assertEqual(len(digests), len(MODULE.ALL_ROLES))

    def test_output_is_reproducible_and_create_only_resume_is_idempotent(self) -> None:
        first = self.fixture.produce()
        first_archives = {
            role: (
                self.fixture.output
                / first["roles"][role]["filename"]
            ).read_bytes()
            for role in MODULE.ALL_ROLES
        }
        second = self.fixture.produce()
        self.assertEqual(
            set(second["publication_results"].values()),
            {"reused"},
        )
        for role in MODULE.ALL_ROLES:
            self.assertEqual(
                (
                    self.fixture.output
                    / second["roles"][role]["filename"]
                ).read_bytes(),
                first_archives[role],
            )

        independent = self.fixture.produce(
            output=self.fixture.output_second,
        )
        for role in MODULE.ALL_ROLES:
            self.assertEqual(
                (
                    self.fixture.output_second
                    / independent["roles"][role]["filename"]
                ).read_bytes(),
                first_archives[role],
            )
            self.assertEqual(
                independent["roles"][role]["sha256"],
                first["roles"][role]["sha256"],
            )

    def test_create_only_publication_recovers_complete_or_partial_temporary(self) -> None:
        destination = self.fixture.output / "one.tar"
        payload = b"bounded-payload"
        temporary = MODULE._temporary_path(destination, payload)
        with mock.patch.object(
            MODULE.os,
            "link",
            side_effect=OSError("simulated crash before publication"),
        ):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                MODULE._publish_create_only(
                    destination,
                    payload,
                    required_uid=os.geteuid(),
                    maximum=1024,
                )
        self.assertFalse(destination.exists())
        self.assertEqual(temporary.read_bytes(), payload)
        self.assertEqual(
            MODULE._publish_create_only(
                destination,
                payload,
                required_uid=os.geteuid(),
                maximum=1024,
            ),
            "created",
        )
        self.assertFalse(temporary.exists())
        self.assertEqual(
            MODULE._publish_create_only(
                destination,
                payload,
                required_uid=os.geteuid(),
                maximum=1024,
            ),
            "reused",
        )
        with self.assertRaisesRegex(
            MODULE.PrepareMaterialError,
            "overwrite different",
        ):
            MODULE._publish_create_only(
                destination,
                b"different",
                required_uid=os.geteuid(),
                maximum=1024,
            )

        linked_destination = self.fixture.output / "linked.tar"
        linked_temporary = MODULE._temporary_path(
            linked_destination,
            payload,
        )
        secure_file(linked_temporary, payload)
        os.link(linked_temporary, linked_destination)
        self.assertEqual(linked_temporary.stat().st_nlink, 2)
        self.assertEqual(
            MODULE._publish_create_only(
                linked_destination,
                payload,
                required_uid=os.geteuid(),
                maximum=1024,
            ),
            "reused",
        )
        self.assertFalse(linked_temporary.exists())
        self.assertEqual(linked_destination.stat().st_nlink, 1)

        partial_destination = self.fixture.output / "partial.tar"
        partial_temporary = MODULE._temporary_path(
            partial_destination,
            payload,
        )
        secure_file(partial_temporary, b"partial")
        self.assertEqual(
            MODULE._publish_create_only(
                partial_destination,
                payload,
                required_uid=os.geteuid(),
                maximum=1024,
            ),
            "created",
        )
        self.assertEqual(partial_destination.read_bytes(), payload)
        self.assertFalse(partial_temporary.exists())

    def test_archive_validator_rejects_traversal_duplicates_specials_and_metadata(self) -> None:
        expected = {"payload": b"x"}

        def archive_bytes(
            specs: list[tuple[str, bytes, dict[str, object]]],
        ) -> bytes:
            stream = io.BytesIO()
            with tarfile.open(
                fileobj=stream,
                mode="w",
                format=tarfile.USTAR_FORMAT,
            ) as archive:
                for name, content, changes in specs:
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    member.mode = 0o600
                    member.uid = 0
                    member.gid = 0
                    member.mtime = 0
                    for key, value in changes.items():
                        setattr(member, key, value)
                    archive.addfile(
                        member,
                        io.BytesIO(content) if member.isreg() else None,
                    )
            return stream.getvalue()

        cases = {
            "traversal": [
                ("../payload", b"x", {}),
            ],
            "duplicate": [
                ("payload", b"x", {}),
                ("payload", b"x", {}),
            ],
            "symlink": [
                (
                    "payload",
                    b"",
                    {"type": tarfile.SYMTYPE, "linkname": "target"},
                ),
            ],
            "device": [
                ("payload", b"", {"type": tarfile.CHRTYPE}),
            ],
            "mode": [
                ("payload", b"x", {"mode": 0o644}),
            ],
            "owner": [
                ("payload", b"x", {"uid": 1000}),
            ],
            "mtime": [
                ("payload", b"x", {"mtime": 1}),
            ],
        }
        for label, specs in cases.items():
            with self.subTest(case=label), self.assertRaises(
                MODULE.PrepareMaterialError
            ):
                MODULE.validate_role_archive_bytes(
                    archive_bytes(specs),
                    expected_files=expected,
                )

    def test_identity_ca_witness_and_stage_tampering_fail_closed(self) -> None:
        original_env = self.fixture.env_path.read_bytes()
        self.fixture.env_path.write_bytes(
            original_env.replace(
                (
                    f"PRODUCTION_SHADOW_OPERATION_ID={OPERATION_ID}"
                ).encode(),
                b"PRODUCTION_SHADOW_OPERATION_ID="
                b"123e4567-e89b-42d3-a456-426614174999",
            )
        )
        self.fixture.env_path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.PrepareMaterialError,
            "differs from operation identity",
        ):
            self.fixture.produce(metadata=False)
        secure_file(self.fixture.env_path, original_env)

        private_key = (
            ed25519.Ed25519PrivateKey.from_private_bytes(b"\x22" * 32)
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        secure_file(self.fixture.ca_path, self.fixture.ca + private_key)
        with self.assertRaisesRegex(
            MODULE.PrepareMaterialError,
            "no private key",
        ):
            self.fixture.produce(metadata=False)
        secure_file(self.fixture.ca_path, self.fixture.ca)

        witness = dict(self.fixture.witness_document)
        witness["issuer_state"] = "old-secret-state"
        secure_file(self.fixture.witness_path, canonical_json(witness))
        with self.assertRaisesRegex(
            MODULE.PrepareMaterialError,
            "fields are not exact",
        ):
            self.fixture.produce(metadata=False)
        secure_file(self.fixture.witness_path, self.fixture.witness_bytes)

        stage = json.loads(canonical_json(self.fixture.stage_document))
        stage["roles"]["webapp_ir"]["precommit_manifest_sha256"] = "f" * 64
        secure_file(self.fixture.stage_path, canonical_json(stage))
        with self.assertRaisesRegex(
            MODULE.PrepareMaterialError,
            "fields.*not exact",
        ):
            self.fixture.produce(metadata=False)

    def test_ca_attestation_hash_time_and_certificate_binding_fail_closed(self) -> None:
        original_env = self.fixture.env_path.read_bytes()
        expected_hash = hashlib.sha256(
            self.fixture.ca_attestation_bytes
        ).hexdigest()
        self.fixture.env_path.write_bytes(
            original_env.replace(
                (
                    "PRODUCTION_SHADOW_DR_TLS_ATTESTATION_SHA256="
                    + expected_hash
                ).encode(),
                (
                    "PRODUCTION_SHADOW_DR_TLS_ATTESTATION_SHA256="
                    + "f" * 64
                ).encode(),
            )
        )
        self.fixture.env_path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.PrepareMaterialError,
            "attestation hash differs",
        ):
            self.fixture.produce(metadata=False)
        secure_file(self.fixture.env_path, original_env)

        cases = {
            "stale": {
                "generated_at": "2020-01-01T00:00:00Z",
            },
            "future": {
                "generated_at": "2098-01-01T00:00:00Z",
            },
            "certificate": {
                "ca_sha256": "f" * 64,
            },
            "private-key-mode": {
                "private_key_mode": "0644",
            },
            "old-material": {
                "old_tls_material_reused": True,
            },
        }
        for label, changes in cases.items():
            with self.subTest(case=label):
                document = dict(
                    self.fixture.ca_attestation_document
                )
                document.update(changes)
                secure_file(
                    self.fixture.ca_attestation_path,
                    canonical_json(document),
                )
                with self.assertRaises(MODULE.PrepareMaterialError):
                    self.fixture.produce(metadata=False)
        secure_file(
            self.fixture.ca_attestation_path,
            self.fixture.ca_attestation_bytes,
        )

        noncanonical = json.dumps(
            self.fixture.ca_attestation_document,
            indent=2,
            sort_keys=True,
        ).encode("ascii")
        secure_file(self.fixture.ca_attestation_path, noncanonical)
        with self.assertRaisesRegex(
            MODULE.PrepareMaterialError,
            "canonical JSON",
        ):
            self.fixture.produce(metadata=False)

    def test_wa_ir_internal_contract_is_exact(self) -> None:
        result = self.fixture.produce(metadata=False)
        archive = (
            self.fixture.output
            / result["roles"]["webapp_ir"]["filename"]
        ).read_bytes()
        members = tar_members(archive)
        manifest = json.loads(
            members[MODULE.FINAL_PREPARE_MANIFEST_NAME][1]
        )
        self.assertEqual(
            manifest["schema"],
            "wa-ir-production-final-prepare-material-v1",
        )
        self.assertEqual(
            set(manifest),
            {
                "schema",
                "operation_id",
                "release_sha",
                "operation_manifest_sha256",
                "stage_attestation_sha256",
                "role",
                "runtime_image_ids",
                "entries",
                "required_env_keys",
            },
        )
        self.assertEqual(
            {
                entry["archive_path"]: entry["destination"]
                for entry in manifest["entries"]
            },
            {
                "role-compose.yml": (
                    "rendered/webapp-ir/docker-compose.yml"
                ),
                "runtime.env.role": (
                    "secrets/webapp-ir/runtime.env.role"
                ),
                "ca.crt": "secrets/tls/ca.crt",
            },
        )

        # On the integration branch, exercise the real WA consumer as soon as
        # its final-prepare loader is present.  The base commit predates that
        # loader, so the explicit schema/destination assertions above remain
        # the contract floor for this isolated commit.
        loader = getattr(
            WA_CONSUMER,
            "_load_final_prepare_manifest_bytes",
            None,
        )
        closure_validator = getattr(
            WA_CONSUMER,
            "_validate_role_local_environment_closure",
            None,
        )
        if callable(loader) and callable(closure_validator):
            stage = self.fixture.stage_document["roles"]["webapp_ir"]
            loaded = loader(
                members[MODULE.FINAL_PREPARE_MANIFEST_NAME][1],
                manifest=SimpleNamespace(
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    canonical_sha256=stage[
                        "stage_operation_manifest_sha256"
                    ],
                ),
                expected_stage_attestation_sha256=stage[
                    "stage_attestation_sha256"
                ],
            )
            compose = self.root / "wa-cross-contract.yml"
            compose.write_bytes(members["role-compose.yml"][1])
            closure_validator(
                compose,
                WA_CONSUMER.parse_safe_dotenv(
                    members["runtime.env.role"][1]
                ),
            )
            self.assertEqual(
                dict(loaded.runtime_image_ids),
                self.fixture.runtime_ids["webapp_ir"],
            )


if __name__ == "__main__":
    unittest.main()
