from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from scripts import produce_production_shadow_prepare_material as PREPARE
from scripts import produce_production_shadow_witness_public_stage as MODULE


OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40
WITNESS_IP = "37.152.191.11"


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def native_manifest_bytes(value: dict[str, str]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("ascii")


def write_file(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def certificate_pair(
    now: datetime,
    *,
    server_ip: str = WITNESS_IP,
    expired: bool = False,
) -> tuple[bytes, bytes]:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Witness Test CA")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
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
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "writer-witness.internal")]
    )
    not_after = (
        now - timedelta(minutes=1)
        if expired
        else now + timedelta(days=30)
    )
    server = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca.subject)
        .public_key(server_key.public_key())
        .serial_number(2)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(not_after)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=True,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address(server_ip))]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return (
        ca.public_bytes(serialization.Encoding.PEM),
        server.public_bytes(serialization.Encoding.DER),
    )


class FakeRunner:
    def __init__(
        self,
        release_root: Path,
        tracked_modes: dict[str, int],
    ) -> None:
        self.release_root = release_root
        self.tracked_modes = tracked_modes
        self.overrides: dict[
            tuple[str, ...], MODULE.CommandResult
        ] = {}
        self.calls: list[tuple[str, ...]] = []

    def command(self, *arguments: str) -> tuple[str, ...]:
        return MODULE._git_command(self.release_root, *arguments)

    def __call__(
        self,
        argv: tuple[str, ...],
        _timeout: float,
    ) -> MODULE.CommandResult:
        self.calls.append(argv)
        if argv in self.overrides:
            return self.overrides[argv]
        if argv == MODULE.SYSTEMD_COMMAND:
            return MODULE.CommandResult(argv, 0, b"active\n", b"")
        if argv == self.command("rev-parse", "--show-toplevel"):
            return MODULE.CommandResult(
                argv,
                0,
                (str(self.release_root) + "\n").encode(),
                b"",
            )
        if argv == self.command(
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
        ):
            return MODULE.CommandResult(
                argv,
                0,
                (RELEASE_SHA + "\n").encode(),
                b"",
            )
        if argv == self.command(
            "rev-parse",
            "--verify",
            "HEAD^{tree}",
        ):
            return MODULE.CommandResult(
                argv,
                0,
                (RELEASE_TREE_SHA + "\n").encode(),
                b"",
            )
        if argv == self.command("symbolic-ref", "-q", "HEAD"):
            return MODULE.CommandResult(argv, 1, b"", b"")
        if argv == self.command(
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ):
            return MODULE.CommandResult(argv, 0, b"", b"")
        if argv == self.command("ls-files", "--stage", "-z"):
            rows = []
            for index, (relative, mode) in enumerate(
                sorted(self.tracked_modes.items()),
                start=1,
            ):
                rows.append(
                    (
                        f"{mode:o} {index:040x} 0\t{relative}"
                    ).encode("utf-8")
                )
            return MODULE.CommandResult(
                argv,
                0,
                b"\0".join(rows) + b"\0",
                b"",
            )
        raise AssertionError(f"unexpected command: {argv!r}")


class WitnessStageFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.release_prefix = root / "staged"
        self.release_root = self.release_prefix / RELEASE_SHA
        self.release_root.mkdir(parents=True, mode=0o755)
        self.release_root.chmod(0o755)
        (self.release_root / ".git").mkdir(mode=0o700)

        self.source_payloads: dict[str, bytes] = {}
        self.tracked_modes: dict[str, int] = {}
        for index, source in enumerate(
            sorted(MODULE.SOURCE_TO_NATIVE_RELEASE),
            start=1,
        ):
            payload = f"witness-source-{index}:{source}\n".encode("ascii")
            self.source_payloads[source] = payload
            write_file(self.release_root / source, payload, 0o644)
            self.tracked_modes[source] = 0o100644

        self.active_root = root / "active-release"
        self.active_root.mkdir(mode=0o755)
        self.active_root.chmod(0o755)
        self.active_manifest = {
            target: hashlib.sha256(
                self.source_payloads[source]
            ).hexdigest()
            for source, target in MODULE.SOURCE_TO_NATIVE_RELEASE.items()
        }
        self.active_manifest_path = (
            self.active_root / "release-manifest.json"
        )
        write_file(
            self.active_manifest_path,
            native_manifest_bytes(self.active_manifest),
            0o644,
        )

        self.ca_pem, self.server_der = certificate_pair(self.now)
        self.ca_path = root / "certificates" / "ca.crt"
        write_file(self.ca_path, self.ca_pem, 0o644)

        self.output = root / "output"
        self.output.mkdir(mode=0o700)
        self.output.chmod(0o700)
        self.runner = FakeRunner(self.release_root, self.tracked_modes)

    @staticmethod
    def http_probe(
        path: str,
        _timeout: float,
    ) -> MODULE.HttpObservation:
        return MODULE.HttpObservation(
            status_code=200,
            content_type="application/json",
            body=MODULE.HEALTH_EXPECTATIONS[path],
        )

    def tls_probe(
        self,
        _ca_pem: bytes,
        _server_name: str,
        _timeout: float,
    ) -> MODULE.TlsObservation:
        return MODULE.TlsObservation(
            peer_certificate_der=self.server_der,
            protocol="TLSv1.3",
            cipher="TLS_AES_256_GCM_SHA384",
        )

    def produce(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_root": self.release_root,
            "release_prefix": self.release_prefix,
            "active_release_root": self.active_root,
            "ca_certificate": self.ca_path,
            "witness_tls_server_name": WITNESS_IP,
            "output_directory": self.output,
            "required_uid": self.uid,
            "required_gid": self.gid,
            "command_runner": self.runner,
            "http_probe": self.http_probe,
            "tls_probe": self.tls_probe,
            "now": self.now,
        }
        values.update(overrides)
        return MODULE.produce_witness_public_stage(**values)

    def rewrite_active_manifest(self) -> None:
        self.active_manifest_path.write_bytes(
            native_manifest_bytes(self.active_manifest)
        )
        self.active_manifest_path.chmod(0o644)


class ProductionShadowWitnessPublicStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.fixture = WitnessStageFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_prepare_compatible_create_only_public_evidence(
        self,
    ) -> None:
        summary = self.fixture.produce()

        self.assertEqual(summary["schema"], MODULE.SUMMARY_SCHEMA)
        self.assertTrue(summary["native_release_reused"])
        self.assertEqual(
            summary["stage_binding"]["runtime_image_ids"],
            {},
        )
        outputs = summary["outputs"]
        self.assertEqual(
            set(outputs),
            {
                MODULE.HEALTH_ATTESTATION_NAME,
                MODULE.PUBLIC_INPUT_NAME,
                MODULE.STAGE_OPERATION_NAME,
                MODULE.CONTROLLER_BINDING_NAME,
            },
        )
        for name, metadata in outputs.items():
            path = self.fixture.output / name
            raw = path.read_bytes()
            document = json.loads(raw)
            self.assertEqual(raw, canonical_json(document))
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(),
                metadata["sha256"],
            )
            self.assertEqual(len(raw), metadata["bytes"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_uid, self.fixture.uid)

        public_path = (
            self.fixture.output / MODULE.PUBLIC_INPUT_NAME
        )
        public_raw = public_path.read_bytes()
        public = json.loads(public_raw)
        self.assertEqual(set(public), MODULE.PUBLIC_INPUT_FIELDS)
        PREPARE._validate_witness_public_input(
            public,
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
        )
        self.assertEqual(
            summary["stage_binding"]["stage_attestation_sha256"],
            hashlib.sha256(public_raw).hexdigest(),
        )
        self.assertTrue(public["native_release_reused"])
        self.assertFalse(public["current_mutated"])
        self.assertFalse(public["service_mutated"])
        self.assertFalse(public["legacy_secret_material_copied"])

        health = json.loads(
            (
                self.fixture.output / MODULE.HEALTH_ATTESTATION_NAME
            ).read_bytes()
        )
        self.assertEqual(set(health), MODULE.HEALTH_ATTESTATION_FIELDS)
        self.assertEqual(
            health["systemd"]["argv"],
            list(MODULE.SYSTEMD_COMMAND),
        )
        self.assertEqual(
            set(health["loopback_http"]),
            set(MODULE.HEALTH_EXPECTATIONS),
        )
        self.assertNotIn("certificate", health["loopback_tls"])

        stage_raw = (
            self.fixture.output / MODULE.STAGE_OPERATION_NAME
        ).read_bytes()
        stage = json.loads(stage_raw)
        self.assertEqual(set(stage), MODULE.STAGE_OPERATION_FIELDS)
        self.assertEqual(stage["runtime_image_ids"], {})
        self.assertEqual(
            stage["stage_attestation_sha256"],
            hashlib.sha256(public_raw).hexdigest(),
        )
        self.assertEqual(
            summary["stage_binding"][
                "stage_operation_manifest_sha256"
            ],
            hashlib.sha256(stage_raw).hexdigest(),
        )
        controller_binding = json.loads(
            (
                self.fixture.output / MODULE.CONTROLLER_BINDING_NAME
            ).read_bytes()
        )
        self.assertEqual(
            set(controller_binding),
            MODULE.CONTROLLER_BINDING_FIELDS,
        )
        self.assertEqual(
            controller_binding["schema"],
            MODULE.CONTROLLER_BINDING_SCHEMA,
        )
        self.assertEqual(controller_binding["role"], "witness")
        self.assertEqual(controller_binding["runtime_image_ids"], {})
        self.assertEqual(
            controller_binding[
                "stage_operation_manifest_sha256"
            ],
            hashlib.sha256(stage_raw).hexdigest(),
        )
        self.assertEqual(
            controller_binding["stage_attestation_sha256"],
            hashlib.sha256(public_raw).hexdigest(),
        )
        self.assertNotIn(
            "candidate_release_root",
            controller_binding,
        )
        self.assertNotIn("ca_sha256", controller_binding)
        all_output = b"".join(
            (self.fixture.output / name).read_bytes()
            for name in outputs
        )
        self.assertNotIn(b"PRIVATE KEY", all_output)
        self.assertNotIn(b"BOT_TOKEN", all_output)
        self.assertNotIn(b"ARVAN", all_output)
        git_calls = [
            argv
            for argv in self.fixture.runner.calls
            if argv[0] == MODULE.GIT_EXECUTABLE
        ]
        self.assertTrue(git_calls)
        for argv in git_calls:
            self.assertEqual(argv[1], "--no-optional-locks")
            self.assertIn("core.fsmonitor=false", argv)
            self.assertIn("core.untrackedCache=false", argv)

    def test_reviewed_mapping_matches_the_real_native_builder(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "native-release"
            environment = dict(os.environ)
            environment[
                "WRITER_WITNESS_RELEASE_MANIFEST_DERIVATION_ONLY"
            ] = "true"
            completed = subprocess.run(
                [
                    "bash",
                    str(
                        repo_root
                        / "scripts/build_writer_witness_release.sh"
                    ),
                    str(destination),
                ],
                cwd=repo_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            built = json.loads(
                (
                    destination / "release-manifest.json"
                ).read_text(encoding="utf-8")
            )
        expected = {
            target: hashlib.sha256(
                (repo_root / source).read_bytes()
            ).hexdigest()
            for source, target in MODULE.SOURCE_TO_NATIVE_RELEASE.items()
        }
        self.assertEqual(built, expected)

    def test_rejects_existing_output_before_collecting_commands(self) -> None:
        write_file(
            self.fixture.output / MODULE.PUBLIC_INPUT_NAME,
            b"existing",
            0o600,
        )
        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "create-only output already exists",
        ):
            self.fixture.produce()
        self.assertEqual(self.fixture.runner.calls, [])

    def test_rejects_foreign_dirty_or_attached_git_release(self) -> None:
        cases = (
            (
                ("rev-parse", "--verify", "HEAD^{commit}"),
                MODULE.CommandResult(
                    self.fixture.runner.command(
                        "rev-parse",
                        "--verify",
                        "HEAD^{commit}",
                    ),
                    0,
                    ("c" * 40 + "\n").encode(),
                    b"",
                ),
                "Git HEAD differs",
            ),
            (
                (
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                ),
                MODULE.CommandResult(
                    self.fixture.runner.command(
                        "status",
                        "--porcelain=v1",
                        "--untracked-files=all",
                    ),
                    0,
                    b" M writer_witness_app.py\n",
                    b"",
                ),
                "worktree is not clean",
            ),
            (
                ("symbolic-ref", "-q", "HEAD"),
                MODULE.CommandResult(
                    self.fixture.runner.command(
                        "symbolic-ref",
                        "-q",
                        "HEAD",
                    ),
                    1,
                    b"refs/heads/main\n",
                    b"",
                ),
                "detached Git HEAD",
            ),
        )
        for arguments, result, expected in cases:
            with self.subTest(expected=expected):
                fixture = WitnessStageFixture(
                    self.root / expected.replace(" ", "-")
                )
                fixture.runner.overrides[
                    fixture.runner.command(*arguments)
                ] = MODULE.CommandResult(
                    fixture.runner.command(*arguments),
                    result.returncode,
                    result.stdout,
                    result.stderr,
                )
                with self.assertRaisesRegex(
                    MODULE.WitnessPublicStageError,
                    expected,
                ):
                    fixture.produce()

    def test_rejects_noncanonical_or_different_active_manifest(self) -> None:
        self.fixture.active_manifest_path.write_bytes(
            native_manifest_bytes(self.fixture.active_manifest) + b"\n"
        )
        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "canonical builder form",
        ):
            self.fixture.produce()

        fixture = WitnessStageFixture(self.root / "different")
        first = next(iter(fixture.active_manifest))
        fixture.active_manifest[first] = "f" * 64
        fixture.rewrite_active_manifest()
        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "candidate Witness subset differs",
        ):
            fixture.produce()

        fixture = WitnessStageFixture(self.root / "extra")
        fixture.active_manifest["unexpected.py"] = "e" * 64
        fixture.rewrite_active_manifest()
        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "reviewed subset",
        ):
            fixture.produce()

    def test_rejects_candidate_symlink_hardlink_and_mode_drift(self) -> None:
        source = next(iter(MODULE.SOURCE_TO_NATIVE_RELEASE))
        source_path = self.fixture.release_root / source
        source_path.unlink()
        source_path.symlink_to("/etc/passwd")
        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "unsafe",
        ):
            self.fixture.produce()

        fixture = WitnessStageFixture(self.root / "hardlink")
        source = next(iter(MODULE.SOURCE_TO_NATIVE_RELEASE))
        os.link(
            fixture.release_root / source,
            fixture.release_root / "unexpected-hardlink",
        )
        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "unsafe",
        ):
            fixture.produce()

        fixture = WitnessStageFixture(self.root / "mode")
        source = next(iter(MODULE.SOURCE_TO_NATIVE_RELEASE))
        (fixture.release_root / source).chmod(0o666)
        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "unsafe",
        ):
            fixture.produce()

    def test_rejects_stale_and_future_health_epochs(self) -> None:
        for offset, expected in (
            (-MODULE.MAX_HEALTH_AGE_SECONDS - 1, "stale"),
            (
                MODULE.MAX_HEALTH_FUTURE_SKEW_SECONDS + 1,
                "future",
            ),
        ):
            fixture = WitnessStageFixture(
                self.root / f"epoch-{offset}"
            )
            with self.assertRaisesRegex(
                MODULE.WitnessPublicStageError,
                expected,
            ):
                fixture.produce(
                    observed_at_epoch=int(
                        fixture.now.timestamp()
                    )
                    + offset
                )

    def test_rejects_bad_systemd_and_health_responses(self) -> None:
        command = MODULE.SYSTEMD_COMMAND
        self.fixture.runner.overrides[command] = MODULE.CommandResult(
            command,
            0,
            b"inactive\n",
            b"",
        )
        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "not exactly active",
        ):
            self.fixture.produce()

        fixture = WitnessStageFixture(self.root / "health")

        def bad_http(
            path: str,
            _timeout: float,
        ) -> MODULE.HttpObservation:
            body = (
                b'{"status":"not_ready"}'
                if path == "/health/ready"
                else MODULE.HEALTH_EXPECTATIONS[path]
            )
            return MODULE.HttpObservation(200, "application/json", body)

        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "health response differs",
        ):
            fixture.produce(http_probe=bad_http)

    def test_rejects_foreign_chain_san_and_expired_certificate(self) -> None:
        cases = (
            ("foreign-chain", WITNESS_IP, False, True),
            ("wrong-san", "192.0.2.10", False, False),
            ("expired", WITNESS_IP, True, False),
        )
        for name, server_ip, expired, replace_ca in cases:
            with self.subTest(name=name):
                fixture = WitnessStageFixture(self.root / name)
                foreign_ca, server_der = certificate_pair(
                    fixture.now,
                    server_ip=server_ip,
                    expired=expired,
                )
                if replace_ca:
                    # The peer is signed by foreign_ca while the trusted local
                    # CA remains the original fixture certificate.
                    _ = foreign_ca
                else:
                    write_file(fixture.ca_path, foreign_ca, 0o644)
                fixture.server_der = server_der
                with self.assertRaisesRegex(
                    MODULE.WitnessPublicStageError,
                    "certificate",
                ):
                    fixture.produce()

    def test_detects_candidate_and_manifest_toctou(self) -> None:
        source = next(iter(MODULE.SOURCE_TO_NATIVE_RELEASE))

        def mutate_candidate(phase: str) -> None:
            if phase == "after-release-subset":
                with (self.fixture.release_root / source).open("ab") as stream:
                    stream.write(b"changed")

        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "changed during attestation",
        ):
            self.fixture.produce(checkpoint=mutate_candidate)

        fixture = WitnessStageFixture(self.root / "manifest-toctou")

        def mutate_manifest(phase: str) -> None:
            if phase == "after-release-subset":
                with fixture.active_manifest_path.open("ab") as stream:
                    stream.write(b"\n")

        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "manifest changed",
        ):
            fixture.produce(checkpoint=mutate_manifest)

        fixture = WitnessStageFixture(self.root / "late-toctou")
        source = next(iter(MODULE.SOURCE_TO_NATIVE_RELEASE))

        def mutate_before_publish(phase: str) -> None:
            if phase == "before-publish":
                with (fixture.release_root / source).open("ab") as stream:
                    stream.write(b"late-change")

        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "before publication",
        ):
            fixture.produce(checkpoint=mutate_before_publish)

    def test_rejects_wrong_staged_path_and_unsafe_ca_mode(self) -> None:
        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "exact staged path",
        ):
            self.fixture.produce(
                release_prefix=self.root / "another-prefix"
            )

        fixture = WitnessStageFixture(self.root / "ca-mode")
        fixture.ca_path.chmod(0o666)
        with self.assertRaisesRegex(
            MODULE.WitnessPublicStageError,
            "CA certificate metadata is unsafe",
        ):
            fixture.produce()

    def test_default_runner_delegates_to_identity_bounded_execution(
        self,
    ) -> None:
        command = ("/usr/bin/git", "--version")
        bounded_result = MODULE.BoundedCommandResult(
            returncode=0,
            stdout=b"git version\n",
            stderr=b"",
        )
        with mock.patch.object(
            MODULE,
            "_bounded_command",
            return_value=bounded_result,
        ) as bounded:
            result = MODULE._default_command_runner(command, 1.25)

        self.assertEqual(
            result,
            MODULE.CommandResult(
                argv=command,
                returncode=0,
                stdout=b"git version\n",
                stderr=b"",
            ),
        )
        bounded.assert_called_once_with(
            command,
            env={
                "GIT_NO_REPLACE_OBJECTS": "1",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LC_ALL": "C",
            },
            timeout=1.25,
            stdout_limit=MODULE.MAX_COMMAND_OUTPUT_BYTES,
            stderr_limit=MODULE.MAX_COMMAND_OUTPUT_BYTES,
        )

    def test_default_runner_maps_bounded_failure_but_not_baseexception(
        self,
    ) -> None:
        command = ("/usr/bin/git", "--version")
        with (
            mock.patch.object(
                MODULE,
                "_bounded_command",
                side_effect=MODULE.BoundedCommandError("timed out"),
            ),
            self.assertRaisesRegex(
                MODULE.WitnessPublicStageError,
                "did not complete safely",
            ),
        ):
            MODULE._default_command_runner(command, 0.1)
        with (
            mock.patch.object(
                MODULE,
                "_bounded_command",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            MODULE._default_command_runner(command, 0.1)


if __name__ == "__main__":
    unittest.main()
