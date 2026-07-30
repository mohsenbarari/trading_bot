from __future__ import annotations

import base64
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_webapp_ir_static_receive.py"
SPEC = importlib.util.spec_from_file_location("render_webapp_ir_static_receive_test", SCRIPT)
assert SPEC and SPEC.loader
receiver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = receiver
SPEC.loader.exec_module(receiver)

INSTALL_SCRIPT = ROOT / "scripts" / "install_webapp_ir_static_assets.py"
INSTALL_SPEC = importlib.util.spec_from_file_location("install_webapp_ir_static_assets_for_receive_test", INSTALL_SCRIPT)
assert INSTALL_SPEC and INSTALL_SPEC.loader
static_installer = importlib.util.module_from_spec(INSTALL_SPEC)
sys.modules[INSTALL_SPEC.name] = static_installer
INSTALL_SPEC.loader.exec_module(static_installer)


CAMPAIGN = "wa-ir-static-receiver-fixture-20260730"
RELEASE = "1" * 40
CONTROL_COMMIT = "2" * 40
CONTROL_TREE = "3" * 40
REVISION = "0123456789ab"
REGION = "ir-thr-at1"
BUCKET = "private-artifacts"
PREFIX = "campaigns/wa-ir-static"
HOST = f"s3.{REGION}.arvanstorage.ir"
CONTROLLER_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
FI_RECIPIENT = "age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"
WA_IR_RECIPIENT = "age1ssssssssssssssssssssssssssssssssssssssss"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def write_archive(path: Path) -> bytes:
    entries = {
        "assets/app.js": b"console.log('fixture');\n",
        "index.html": b"<!doctype html><title>fixture</title>\n",
    }
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(entries):
            payload = entries[name]
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    path.chmod(0o600)
    return path.read_bytes()


class RenderWebappIrStaticReceiveTests(unittest.TestCase):
    def _transport_config(self, root: Path) -> Path:
        path = root / "controller" / "source-transport-config.json"
        value: dict[str, object] = {
            "schema": receiver.transport.CONFIG_SCHEMA,
            "endpoint": f"https://{HOST}",
            "region": REGION,
            "bucket": BUCKET,
            "prefix": PREFIX,
            "credentials_file": "/root/secure-envs/trading-bot/nontransferred-controller-s3.json",
            "age_binary": "/usr/bin/age",
            "workspace": str(root / "controller" / "workspace"),
            "controller_age_recipient": CONTROLLER_RECIPIENT,
            "webapp_fi_age_recipient": FI_RECIPIENT,
            "webapp_ir_age_recipient": WA_IR_RECIPIENT,
            "maximum_plaintext_bytes": receiver.MAX_STATIC_ARCHIVE_BYTES,
            "presign_expires_seconds": 300,
        }
        return write_private(path, canonical(value))

    def _url(self, *, object_key: str, version_id: str, now: dt.datetime, expires: int = 300) -> str:
        credential = quote(f"FIXTURE/{now:%Y%m%d}/{REGION}/s3/aws4_request", safe="")
        return (
            f"https://{HOST}/{BUCKET}/{quote(object_key, safe='/')}?versionId={quote(version_id, safe='')}"
            f"&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential={credential}"
            f"&X-Amz-Date={now:%Y%m%dT%H%M%SZ}&X-Amz-Expires={expires}&X-Amz-SignedHeaders=host"
            f"&X-Amz-Signature={'a' * 64}"
        )

    def _fixture(self, root: Path, *, now: dt.datetime) -> dict[str, object]:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        config_path = self._transport_config(root)
        policy = receiver.transport.load_controller_config(config_path).policy
        archive_path = root / "source" / "static.tar"
        archive_path.parent.mkdir(mode=0o700)
        archive = write_archive(archive_path)
        request = receiver.transport.SourceObjectRequest(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            control_commit=CONTROL_COMMIT,
            control_tree=CONTROL_TREE,
            source_site="webapp_fi",
            destination_site=receiver.transport.STATIC_DESTINATION_SITE,
            object_kind=receiver.transport.STATIC_OBJECT_KIND,
            object_id="static-source-20260730",
            mode=receiver.transport.STATIC_MODE,
            recipients=(CONTROLLER_RECIPIENT, WA_IR_RECIPIENT),
        )
        descriptor: dict[str, object] = {
            "object_key": receiver.transport.source_object_key(policy, request),
            "version_id": "fixture-static-version-001",
            # The fake age copies test bytes.  Production binds a real age ciphertext.
            "ciphertext_sha256": digest(archive),
            "ciphertext_bytes": len(archive),
            "plaintext_sha256": digest(archive),
            "plaintext_bytes": len(archive),
        }
        published = receiver.transport.build_publish_receipt(config=policy, request=request, descriptor=descriptor)
        publish_path = write_private(root / "receipts" / "static-publish.json", canonical(published))
        controller = Ed25519PrivateKey.generate()
        controller_public = base64.b64encode(
            controller.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        ).decode("ascii")
        files = [
            {"path": "assets/app.js", "sha256": digest(b"console.log('fixture');\n"), "bytes": len(b"console.log('fixture');\n")},
            {"path": "index.html", "sha256": digest(b"<!doctype html><title>fixture</title>\n"), "bytes": len(b"<!doctype html><title>fixture</title>\n")},
        ]
        unsigned: dict[str, object] = {
            "schema": receiver.provenance.STATIC_ASSET_PROVENANCE_SCHEMA,
            "status": "verified",
            "campaign_id": CAMPAIGN,
            "application": {"release_sha": RELEASE, "expected_alembic_revision": REVISION},
            "source_kind": "deterministic_2c08_dist_manifest",
            "artifact": descriptor,
            "files": files,
            "files_sha256": digest(receiver.canonical_json_bytes(files)),
            "controller_public_key_base64": controller_public,
        }
        signature = controller.sign(
            receiver.provenance.STATIC_ASSET_SIGNATURE_DOMAIN + receiver.canonical_json_bytes(unsigned)
        )
        proof = {
            **unsigned,
            "controller_signature": {"algorithm": "ed25519", "signature_base64": base64.b64encode(signature).decode("ascii")},
        }
        proof_path = write_private(root / "receipts" / "static-proof.json", canonical(proof))
        return {
            "transport_config": config_path,
            "publish": publish_path,
            "proof": proof_path,
            "published": published,
            "proof_value": proof,
            "archive": archive_path,
            "archive_bytes": archive,
            "pinned_key": controller_public,
            "url": self._url(object_key=str(descriptor["object_key"]), version_id=str(descriptor["version_id"]), now=now),
        }

    def _render(self, fixture: dict[str, object]) -> str:
        return receiver.render_receive_command(
            transport_publish_receipt=Path(fixture["publish"]),
            source_transport_config=Path(fixture["transport_config"]),
            static_assets_provenance=Path(fixture["proof"]),
            pinned_controller_public_key_base64=str(fixture["pinned_key"]),
            presigned_url=str(fixture["url"]),
        )

    def _remote(self, command: str) -> tuple[list[str], str, dict[str, object]]:
        outer = shlex.split(command)
        self.assertEqual(outer[:5], ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"])
        self.assertEqual(outer[5], receiver.REMOTE_HOST)
        self.assertEqual(len(outer), 7)
        inner = shlex.split(outer[6])
        self.assertEqual(inner[:5], ["/usr/bin/python3", "-I", "-B", "-c", receiver.REMOTE_LAUNCHER])
        self.assertEqual(inner[7], "--")
        return inner, base64.b64decode(inner[5]).decode("utf-8"), json.loads(base64.b64decode(inner[6]).decode("ascii"))

    def test_renderer_uses_only_url_free_signed_control_metadata(self) -> None:
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        with tempfile.TemporaryDirectory(prefix="wa-ir-static-render-") as temporary:
            fixture = self._fixture(Path(temporary), now=now)
            with mock.patch.object(receiver, "_utc_now", return_value=now):
                command = self._render(fixture)
            inner, program, config = self._remote(command)
            self.assertEqual(inner[-1], fixture["url"])
            self.assertEqual(inner.count(fixture["url"]), 1)
            self.assertEqual(set(config), set(receiver.REMOTE_CONFIG_FIELDS))
            self.assertEqual(set(config["object_storage"]), set(receiver.REMOTE_STORAGE_FIELDS))
            self.assertEqual(config["receiver_root"], receiver.DEFAULT_RECEIVER_ROOT)
            self.assertEqual(config["age_identity_file"], receiver.wa_ir_bootstrap_identity_file(CAMPAIGN))
            self.assertEqual(config["transport_receipt"]["object"], config["static_assets_provenance"]["artifact"])
            durable = receiver.canonical_json_bytes(config)
            self.assertNotIn(Path(fixture["archive"]).read_bytes(), durable)
            self.assertNotIn(b"nontransferred-controller-s3", durable)
            self.assertNotIn(b"credentials_file", durable)
            self.assertNotIn(str(fixture["url"]).encode("ascii"), durable)
            self.assertNotIn(b"presigned", durable.lower())
            self.assertNotIn(b"X-Amz-", durable)
            self.assertNotIn(str(fixture["url"]), program)
            self.assertIn("provider-side encryption is disallowed", program)
            self.assertIn("static proof signature verification failed", program)
            self.assertIn("static-assets.tar", program)
            self.assertNotIn("docker", program.lower())
            self.assertIsNone(receiver._assert_control_only_remote_config(config))

    def test_embedded_receiver_tracks_the_current_static_transport_contract_without_campaign_pins(self) -> None:
        program = receiver.REMOTE_RECEIVER_SOURCE
        self.assertIn(f'TRANSPORT_SCHEMA = "{receiver.transport.TRANSPORT_SCHEMA}"', program)
        self.assertIn(f'OBJECT_ENCRYPTION = "{receiver.transport.OBJECT_ENCRYPTION}"', program)
        self.assertIn(f'"{receiver.transport.OBJECT_LAYOUT_VERSION}"', program)
        self.assertIn(f'"{receiver.transport.STATIC_DESTINATION_SITE}"', program)
        self.assertIn(f'"{receiver.transport.STATIC_MODE}"', program)
        self.assertIn(
            f"STATIC_RECEIVE_RECEIPT_RESERVE_BYTES = {receiver.STATIC_RECEIVE_RECEIPT_RESERVE_BYTES}",
            program,
        )
        self.assertIn(
            f"STATIC_RECEIVE_CAPACITY_MARGIN_BYTES = {receiver.STATIC_RECEIVE_CAPACITY_MARGIN_BYTES}",
            program,
        )
        self.assertNotRegex(program, r"\b[0-9a-f]{40}\b")

    def test_renderer_rejects_wrong_proof_or_url_before_any_ssh_command(self) -> None:
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        with tempfile.TemporaryDirectory(prefix="wa-ir-static-render-") as temporary:
            fixture = self._fixture(Path(temporary), now=now)
            proof = json.loads(Path(fixture["proof"]).read_bytes())
            proof["artifact"]["version_id"] = "forged-version"
            write_private(Path(fixture["proof"]), canonical(proof))
            with mock.patch.object(receiver, "_utc_now", return_value=now), self.assertRaisesRegex(
                receiver.StaticReceiveRenderError, "static asset provenance is unsafe"
            ):
                self._render(fixture)

            fixture = self._fixture(Path(temporary) / "second", now=now)
            wrong_url = str(fixture["url"]).replace("versionId=fixture-static-version-001", "versionId=wrong")
            with mock.patch.object(receiver, "_utc_now", return_value=now), self.assertRaisesRegex(
                receiver.StaticReceiveRenderError, "exact immutable static object"
            ):
                receiver.render_receive_command(
                    transport_publish_receipt=Path(fixture["publish"]),
                    source_transport_config=Path(fixture["transport_config"]),
                    static_assets_provenance=Path(fixture["proof"]),
                    pinned_controller_public_key_base64=str(fixture["pinned_key"]),
                    presigned_url=wrong_url,
                )
            expired_url = str(fixture["url"]).replace(f"X-Amz-Date={now:%Y%m%dT%H%M%SZ}", f"X-Amz-Date={(now - dt.timedelta(hours=1)):%Y%m%dT%H%M%SZ}")
            with mock.patch.object(receiver, "_utc_now", return_value=now), self.assertRaisesRegex(
                receiver.StaticReceiveRenderError, "not current and short-lived"
            ):
                receiver.render_receive_command(
                    transport_publish_receipt=Path(fixture["publish"]),
                    source_transport_config=Path(fixture["transport_config"]),
                    static_assets_provenance=Path(fixture["proof"]),
                    pinned_controller_public_key_base64=str(fixture["pinned_key"]),
                    presigned_url=expired_url,
                )

    def test_persisted_url_rejection_requires_an_actual_url_not_an_opaque_token(self) -> None:
        receiver._reject_persisted_url(
            b'{"object_id":"x-amz-presigned-url"}\n',
            field="fixture control metadata",
        )
        with self.assertRaisesRegex(receiver.StaticReceiveRenderError, "must not persist"):
            receiver._reject_persisted_url(
                b'{"unexpected":"https://example.invalid/object"}\n',
                field="fixture control metadata",
            )

    def test_exact_sibling_loader_refuses_a_writable_checkout_sibling(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wa-ir-static-loader-") as temporary:
            root = Path(temporary)
            source = root / "renderer.py"
            sibling = root / "unsafe-sibling.py"
            source.write_text("# trusted fixture\n", encoding="ascii")
            source.chmod(0o600)
            sibling.write_text("raise RuntimeError('must not load')\n", encoding="ascii")
            sibling.chmod(0o666)
            with mock.patch.object(receiver, "__file__", str(source)), self.assertRaisesRegex(
                RuntimeError, "root-controlled regular file"
            ):
                receiver._load_exact_sibling(sibling.name, "unsafe_wa_ir_static_sibling")

    def _make_fake_executables(
        self,
        root: Path,
        *,
        archive: Path,
        object_value: dict[str, object],
        wrong_version: bool = False,
        sse: bool = False,
        corrupt: bool = False,
    ) -> tuple[Path, Path]:
        curl = root / "fake-curl"
        age = root / "fake-age"
        source = archive
        if corrupt:
            source = root / "corrupt-ciphertext"
            original = archive.read_bytes()
            source.write_bytes((b"X" if original[:1] != b"X" else b"Y") + original[1:])
            source.chmod(0o600)
        version = "wrong-version" if wrong_version else object_value["version_id"]
        sse_header = "x-amz-server-side-encryption: AES256\\r\\n" if sse else ""
        curl.write_text(
            "#!/bin/sh\n"
            "out=''\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = '--output' ]; then out=\"$2\"; shift 2; continue; fi\n"
            "  shift\n"
            "done\n"
            f"cp {shlex.quote(str(source))} \"$out\" || exit 12\n"
            "printf 'HTTP/1.1 200 OK\\r\\n'\n"
            f"printf 'x-amz-version-id: {version}\\r\\n'\n"
            f"printf 'x-amz-meta-transport-schema: {receiver.transport.TRANSPORT_SCHEMA}\\r\\n'\n"
            "printf 'x-amz-meta-encryption: age-v1\\r\\n'\n"
            f"printf 'x-amz-meta-ciphertext-sha256: {object_value['ciphertext_sha256']}\\r\\n'\n"
            "printf 'x-amz-meta-recipient-mode: static\\r\\n'\n"
            + (f"printf '{sse_header}'\n" if sse_header else "")
            + f"printf 'content-length: {object_value['ciphertext_bytes']}\\r\\n\\r\\n'\n",
            encoding="ascii",
        )
        age.write_text(
            "#!/bin/sh\n"
            "out=''\nlast=''\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = '--output' ]; then out=\"$2\"; shift 2; continue; fi\n"
            "  last=\"$1\"; shift\n"
            "done\n"
            "cp \"$last\" \"$out\"\n",
            encoding="ascii",
        )
        for executable in (curl, age):
            executable.chmod(0o755)
        return curl, age

    def _execution_inputs(
        self,
        *,
        wrong_version: bool = False,
        sse: bool = False,
        corrupt: bool = False,
    ) -> tuple[tempfile.TemporaryDirectory[str], dict[str, object], str, Path, str]:
        temporary = tempfile.TemporaryDirectory(prefix="wa-ir-static-receiver-")
        root = Path(temporary.name)
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        fixture = self._fixture(root / "fixture", now=now)
        with mock.patch.object(receiver, "_utc_now", return_value=now):
            command = self._render(fixture)
        _inner, program, config = self._remote(command)
        receiver_root = root / "receiver-root"
        receiver_root.mkdir(mode=0o700)
        identity_root = root / "identities"
        identity = identity_root / CAMPAIGN / "webapp-ir" / "bootstrap.agekey"
        for directory in (identity_root, identity_root / CAMPAIGN, identity.parent):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        identity.write_text("AGE-SECRET-KEY-1TEST\n", encoding="ascii")
        identity.chmod(0o600)
        curl, age = self._make_fake_executables(
            root,
            archive=Path(fixture["archive"]),
            object_value=dict(fixture["published"])["object"],
            wrong_version=wrong_version,
            sse=sse,
            corrupt=corrupt,
        )
        program = (
            program.replace(receiver.DEFAULT_RECEIVER_ROOT, str(receiver_root))
            .replace(receiver.WA_IR_CAMPAIGN_IDENTITY_ROOT, str(identity_root))
            .replace("/usr/bin/curl", str(curl))
            .replace("/usr/bin/age", str(age))
        )
        config["receiver_root"] = str(receiver_root)
        config["age_identity_file"] = str(identity)
        return temporary, config, str(fixture["url"]), receiver_root, program

    def _execute_remote(self, program: str, config: dict[str, object], url: str) -> tuple[int, str]:
        encoded = base64.b64encode(receiver.canonical_json_bytes(config)).decode("ascii")
        stdout = io.StringIO()
        namespace = {"__name__": "__main__"}
        with mock.patch.object(sys, "argv", ["receiver", "program", encoded, "--", url]), contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as exited:
                exec(compile(program, "<test-wa-ir-static-receiver>", "exec"), namespace)
        return int(exited.exception.code), stdout.getvalue()

    @unittest.skipUnless(os.geteuid() == 0, "receiver verifies root-only state")
    def test_embedded_receiver_downloads_and_decrypts_only_to_a_new_static_candidate(self) -> None:
        temporary, config, url, receiver_root, program = self._execution_inputs()
        try:
            result, output = self._execute_remote(program, config, url)
            self.assertEqual(result, 0, output)
            self.assertIn('"status": "received"', output)
            candidates = list(receiver_root.iterdir())
            self.assertEqual(1, len(candidates))
            candidate = candidates[0]
            self.assertTrue(candidate.name.startswith("static-"))
            self.assertEqual(0o700, mode(candidate))
            archive = candidate / receiver.STATIC_ARCHIVE_NAME
            receipt = candidate / receiver.STATIC_RECEIPT_NAME
            self.assertTrue(archive.is_file())
            self.assertTrue(receipt.is_file())
            self.assertFalse((candidate / ".ciphertext.age").exists())
            self.assertEqual(0o600, mode(archive))
            self.assertEqual(0o600, mode(receipt))
            value = json.loads(receipt.read_bytes())
            self.assertEqual(receiver.STATIC_RECEIVE_RECEIPT_SCHEMA, value["schema"])
            self.assertEqual("read_back", value["status"])
            self.assertEqual(CAMPAIGN, value["campaign_id"])
            self.assertEqual(config["transport_receipt"]["object"], value["object"])
            self.assertEqual(
                {"transport": "private_versioned_age_only", "create_only": True, "read_back_same_version_id": True, "provider_side_sse": False},
                value["transport"],
            )
            static_installer._load_receive_record(
                path=receipt,
                expected_campaign_id=CAMPAIGN,
                expected_object=config["transport_receipt"]["object"],
            )
            self.assertNotIn(b"X-Amz-", receipt.read_bytes())
            self.assertNotIn(url.encode("ascii"), receipt.read_bytes())
            self.assertNotIn(b"X-Amz-", archive.read_bytes())
            self.assertNotIn(url.encode("ascii"), archive.read_bytes())
        finally:
            temporary.cleanup()

    @unittest.skipUnless(os.geteuid() == 0, "receiver verifies root-only state")
    def test_embedded_receiver_rejects_wrong_version_sse_and_cipher_hash(self) -> None:
        for name, options in (
            ("version", {"wrong_version": True}),
            ("sse", {"sse": True}),
            ("cipher-hash", {"corrupt": True}),
        ):
            with self.subTest(case=name):
                temporary, config, url, receiver_root, program = self._execution_inputs(**options)
                try:
                    result, output = self._execute_remote(program, config, url)
                    self.assertEqual(result, 2, output)
                    self.assertIn('"status": "blocked"', output)
                    candidates = list(receiver_root.iterdir())
                    self.assertEqual(1, len(candidates), "failed candidate must remain as root-only evidence")
                    self.assertFalse((candidates[0] / receiver.STATIC_RECEIPT_NAME).exists())
                finally:
                    temporary.cleanup()

    @unittest.skipUnless(os.geteuid() == 0, "receiver verifies root-only state")
    def test_embedded_receiver_blocks_for_capacity_before_creating_a_candidate(self) -> None:
        temporary, config, url, receiver_root, program = self._execution_inputs()
        try:
            unavailable = type("Statvfs", (), {"f_bavail": 0, "f_frsize": 4096, "f_flag": 0})()
            with mock.patch.object(os, "statvfs", return_value=unavailable):
                result, output = self._execute_remote(program, config, url)
            self.assertEqual(result, 2, output)
            self.assertIn('"status": "blocked"', output)
            self.assertEqual([], list(receiver_root.iterdir()))
        finally:
            temporary.cleanup()

    @unittest.skipUnless(os.geteuid() == 0, "receiver verifies root-only state")
    def test_embedded_receiver_blocks_read_only_staging_before_candidate_or_curl(self) -> None:
        temporary, config, url, receiver_root, program = self._execution_inputs()
        try:
            read_only = type(
                "Statvfs",
                (),
                {"f_bavail": 1024 * 1024, "f_frsize": 4096, "f_flag": os.ST_RDONLY},
            )()
            with (
                mock.patch.object(os, "statvfs", return_value=read_only),
                mock.patch("subprocess.run") as run,
            ):
                result, output = self._execute_remote(program, config, url)
            self.assertEqual(result, 2, output)
            self.assertIn('"status": "blocked"', output)
            self.assertEqual([], list(receiver_root.iterdir()))
            run.assert_not_called()
        finally:
            temporary.cleanup()

    @unittest.skipUnless(os.geteuid() == 0, "receiver verifies root-only state")
    def test_embedded_receiver_rejects_malformed_staging_mount_status_before_download(self) -> None:
        for name, state, readonly_flag in (
            ("missing-flag", type("Statvfs", (), {"f_bavail": 1, "f_frsize": 4096})(), os.ST_RDONLY),
            ("negative-flag", type("Statvfs", (), {"f_bavail": 1, "f_frsize": 4096, "f_flag": -1})(), os.ST_RDONLY),
            ("invalid-readonly-flag", type("Statvfs", (), {"f_bavail": 1, "f_frsize": 4096, "f_flag": 0})(), False),
        ):
            with self.subTest(case=name):
                temporary, config, url, receiver_root, program = self._execution_inputs()
                try:
                    with (
                        mock.patch.object(os, "statvfs", return_value=state),
                        mock.patch.object(os, "ST_RDONLY", readonly_flag),
                        mock.patch("subprocess.run") as run,
                    ):
                        result, output = self._execute_remote(program, config, url)
                    self.assertEqual(result, 2, output)
                    self.assertIn('"status": "blocked"', output)
                    self.assertEqual([], list(receiver_root.iterdir()))
                    run.assert_not_called()
                finally:
                    temporary.cleanup()

    @unittest.skipUnless(os.geteuid() == 0, "receiver verifies root-only state")
    def test_embedded_receiver_rejects_tampered_proof_and_noncampaign_identity_before_download(self) -> None:
        temporary, config, url, receiver_root, program = self._execution_inputs()
        try:
            proof = dict(config["static_assets_provenance"])
            proof["files_sha256"] = "0" * 64
            config["static_assets_provenance"] = proof
            result, output = self._execute_remote(program, config, url)
            self.assertEqual(result, 2, output)
            self.assertEqual([], list(receiver_root.iterdir()))

            temporary.cleanup()
            temporary, config, url, receiver_root, program = self._execution_inputs()
            config["age_identity_file"] = "/etc/trading-bot-three-site/legacy/wa-ir/bootstrap.agekey"
            result, output = self._execute_remote(program, config, url)
            self.assertEqual(result, 2, output)
            self.assertEqual([], list(receiver_root.iterdir()))
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
