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
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/render_webapp_fi_source_bootstrap_receive.py"
PREPARER_SCRIPT = ROOT / "scripts/prepare_webapp_fi_source_adoption.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


renderer = _load_module("render_webapp_fi_source_bootstrap_receive_test", SCRIPT)
preparer = _load_module("prepare_webapp_fi_source_adoption_for_receiver_test", PREPARER_SCRIPT)


CAMPAIGN_ID = "receiver-test-campaign-20260730"
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
CONTROLLER_RECIPIENT = "age1pppppppppppppppppppppppppppppppppppppppp"
WA_IR_RECIPIENT = "age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr"
REGION = "ir-thr-at1"
BUCKET = "three-site-private"
PREFIX = "campaign-current/artifacts"
HOST = "s3.ir-thr-at1.arvanstorage.ir"
REVISION = "0123456789ab"
NOW = dt.datetime(2026, 7, 30, 12, 0, tzinfo=dt.timezone.utc)
CONTROLLER_CREDENTIAL_MARKER = "DO-NOT-TRANSPORT-CONTROLLER-CREDENTIAL-TEST"


def canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    path.chmod(0o600)


def git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def commit(repository: Path) -> str:
    git(repository, "add", ".")
    git(repository, "commit", "-m", "fixture")
    return subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()


class RenderWebAppFiSourceBootstrapReceiveTests(unittest.TestCase):
    def _transport_config(self, root: Path) -> Path:
        path = root / "controller" / "source-transport-config.json"
        value: dict[str, object] = {
            "schema": renderer.transport.CONFIG_SCHEMA,
            "endpoint": f"https://{HOST}",
            "region": REGION,
            "bucket": BUCKET,
            "prefix": PREFIX,
            "credentials_file": f"/root/secure-envs/trading-bot/{CONTROLLER_CREDENTIAL_MARKER}.json",
            "age_binary": "/usr/bin/age",
            "workspace": str(root / "controller" / "workspace"),
            "controller_age_recipient": CONTROLLER_RECIPIENT,
            "webapp_fi_age_recipient": RECIPIENT,
            "webapp_ir_age_recipient": WA_IR_RECIPIENT,
            "maximum_plaintext_bytes": 24 * 1024 * 1024,
            "presign_expires_seconds": 300,
        }
        write_private(path, canonical(value))
        return path

    def _fixture(self, root: Path, *, now: dt.datetime = NOW) -> dict[str, object]:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        control = root / "control"
        application = root / "application"
        for repository in (control, application):
            repository.mkdir(mode=0o700)
            git(repository, "init")
            git(repository, "config", "user.email", "test@example.invalid")
            git(repository, "config", "user.name", "Receiver Test")
        for relative in preparer.SOURCE_PAYLOAD_FILES:
            target = control / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        control_commit = commit(control)
        control_tree = subprocess.check_output(
            ["git", "-C", str(control), "rev-parse", control_commit + "^{tree}"], text=True
        ).strip()
        (application / "main.py").write_text("print('fixture')\n", encoding="ascii")
        application_release = commit(application)
        packages = root / "packages"
        packages.mkdir(mode=0o700)
        package = packages / "source-bootstrap"
        prepared = preparer.prepare_source_adoption_package(
            source_repository=control,
            application_source_repository=application,
            control_commit=control_commit,
            application_release_sha=application_release,
            expected_alembic_revision=REVISION,
            package_id="source-bootstrap",
            destination=package,
            apply=True,
        )
        transport_config = self._transport_config(root)
        policy = renderer.transport.load_controller_config(transport_config).policy
        request = renderer.transport.SourceObjectRequest(
            campaign_id=CAMPAIGN_ID,
            release_sha=application_release,
            control_commit=control_commit,
            control_tree=control_tree,
            source_site="bot_fi",
            destination_site="webapp_fi",
            object_kind=renderer.transport.BOOTSTRAP_OBJECT_KIND,
            object_id="source-bootstrap",
            mode=renderer.transport.SINGLE_MODE,
            recipients=(RECIPIENT,),
        )
        object_key = renderer.transport.source_object_key(policy, request)
        archive = package / renderer.PACKAGE_ARCHIVE_NAME
        archive_bytes = archive.read_bytes()
        descriptor: dict[str, object] = {
            "object_key": object_key,
            "version_id": "version-001",
            # The receiver test's fake age leaves the bytes unchanged.  The
            # production record remains an age ciphertext at this boundary.
            "ciphertext_sha256": digest(archive_bytes),
            "ciphertext_bytes": len(archive_bytes),
            "plaintext_sha256": digest(archive_bytes),
            "plaintext_bytes": len(archive_bytes),
        }
        published = renderer.transport.build_publish_receipt(config=policy, request=request, descriptor=descriptor)
        publish_path = root / "receipts" / "source-transport-publish.json"
        write_private(publish_path, canonical(published))
        private_key = root / "controller" / "delivery-ed25519.raw"
        private_key.parent.mkdir(mode=0o700, exist_ok=True)
        private_key.write_bytes(os.urandom(32))
        private_key.chmod(0o600)
        envelope_path = root / "receipts" / "delivery-envelope.json"
        envelope_result = preparer.sign_delivery_envelope(
            package_directory=package,
            preparation_receipt=package / preparer.PREPARATION_RECEIPT_NAME,
            expected_control_commit=control_commit,
            expected_application_release_sha=application_release,
            campaign_id=CAMPAIGN_ID,
            fi_bootstrap_recipient=RECIPIENT,
            object_key=object_key,
            version_id="version-001",
            ciphertext_sha256=descriptor["ciphertext_sha256"],
            ciphertext_bytes=descriptor["ciphertext_bytes"],
            plaintext_sha256=descriptor["plaintext_sha256"],
            plaintext_bytes=descriptor["plaintext_bytes"],
            controller_signing_private_key=private_key,
            destination=envelope_path,
            apply=True,
        )
        date = now.strftime("%Y%m%dT%H%M%SZ")
        url = (
            f"https://{HOST}/{BUCKET}/{object_key}?versionId=version-001"
            f"&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=credential"
            f"&X-Amz-Date={date}&X-Amz-Expires=300&X-Amz-SignedHeaders=host&X-Amz-Signature=signature"
        )
        return {
            "package": package,
            "preparation": package / preparer.PREPARATION_RECEIPT_NAME,
            "publish": publish_path,
            "transport_config": transport_config,
            "envelope": envelope_path,
            "pinned_key": envelope_result["controller_public_key_base64"],
            "published": published,
            "url": url,
            "prepared": prepared,
            "archive": archive,
            "control_commit": control_commit,
            "control_tree": control_tree,
        }

    def _render(self, fixture: dict[str, object], *, url: str | None = None) -> str:
        return renderer.render_receive_command(
            transport_publish_receipt=fixture["publish"],
            source_transport_config=fixture["transport_config"],
            source_adoption_package_directory=fixture["package"],
            preparation_receipt=fixture["preparation"],
            delivery_envelope=fixture["envelope"],
            pinned_controller_public_key_base64=str(fixture["pinned_key"]),
            presigned_url=str(fixture["url"] if url is None else url),
        )

    def _remote(self, command: str) -> tuple[list[str], str, dict[str, object]]:
        outer = shlex.split(command)
        self.assertEqual(outer[:5], ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"])
        self.assertEqual(outer[5], renderer.REMOTE_HOST)
        self.assertEqual(len(outer), 7)
        inner = shlex.split(outer[6])
        self.assertEqual(inner[:5], ["/usr/bin/python3", "-I", "-B", "-c", renderer.REMOTE_LAUNCHER])
        self.assertEqual(inner[7], "--")
        program = base64.b64decode(inner[5]).decode("utf-8")
        config = json.loads(base64.b64decode(inner[6]).decode("utf-8"))
        return inner, program, config

    def test_happy_render_uses_generic_receipt_and_url_free_signed_control_metadata(self):
        with tempfile.TemporaryDirectory(prefix="fi-bootstrap-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            with mock.patch.object(renderer, "_utc_now", return_value=NOW):
                command = self._render(fixture)
            inner, program, config = self._remote(command)
            self.assertEqual(len(inner), 9)
            self.assertEqual(inner[-1], fixture["url"])
            self.assertEqual(inner.count(fixture["url"]), 1)
            self.assertEqual(config["schema"], renderer.RECEIVER_CONFIG_SCHEMA)
            self.assertEqual(set(config), set(renderer.REMOTE_CONFIG_FIELDS))
            self.assertEqual(set(config["object_storage"]), set(renderer.REMOTE_CONFIG_STORAGE_FIELDS))
            self.assertEqual(config["receiver_root"], renderer.DEFAULT_RECEIVER_ROOT)
            self.assertEqual(
                config["age_identity_file"],
                renderer.webapp_fi_bootstrap_identity_file(config["campaign_id"]),
            )
            self.assertEqual(
                set(config["transport_receipt"]),
                {
                    "schema",
                    "status",
                    "campaign_id",
                    "release_sha",
                    "control_commit",
                    "control_tree",
                    "source_site",
                    "destination_site",
                    "object_kind",
                    "object_id",
                    "recipient_mode",
                    "recipients",
                    "transport",
                    "object",
                    "receipt_sha256",
                },
            )
            self.assertEqual(
                set(config["preparation_receipt"]),
                {
                    "schema",
                    "status",
                    "package_id",
                    "package_directory",
                    "source_site",
                    "destination_site",
                    "application",
                    "tooling",
                    "archive",
                    "package_manifest",
                    "receipt_sha256",
                },
            )
            self.assertEqual(
                set(config["delivery_envelope"]),
                {
                    "schema",
                    "status",
                    "campaign_id",
                    "source_site",
                    "destination_site",
                    "package_id",
                    "application",
                    "tooling",
                    "canonical_release_tree_sha256",
                    "fi_bootstrap_recipient",
                    "object",
                    "controller_public_key_base64",
                    "controller_signature",
                },
            )
            self.assertEqual(config["transport_receipt"]["schema"], renderer.transport.TRANSPORT_SCHEMA)
            self.assertEqual(config["transport_receipt"]["source_site"], "bot_fi")
            self.assertEqual(config["transport_receipt"]["destination_site"], "webapp_fi")
            self.assertEqual(config["transport_receipt"]["object_kind"], "bootstrap_package")
            self.assertEqual(config["transport_receipt"]["recipient_mode"], "single")
            self.assertEqual(config["transport_receipt"]["recipients"], [RECIPIENT])
            self.assertEqual(config["delivery_envelope"]["object"], config["transport_receipt"]["object"])
            durable = json.dumps(config, sort_keys=True).encode("utf-8")
            archive_bytes = Path(fixture["archive"]).read_bytes()
            self.assertNotIn(archive_bytes, durable)
            self.assertNotIn(base64.b64encode(archive_bytes), durable)
            self.assertNotIn(CONTROLLER_CREDENTIAL_MARKER.encode("ascii"), durable)
            self.assertNotIn(b"/etc/trading-bot-three-site/webapp-fi/source-adoption-bootstrap.agekey", durable)
            for forbidden in (b"credentials_file", b"access_key", b"secret_key", b"session_token", b"password", b"private_key"):
                self.assertNotIn(forbidden, durable)
            self.assertNotIn(b"presigned", durable.lower())
            self.assertNotIn(b"x-amz-", durable.lower())
            self.assertNotIn(str(fixture["url"]).encode("utf-8"), durable)
            self.assertNotIn(str(fixture["url"]), program)
            self.assertIsNone(renderer._assert_control_only_remote_config(config))
            unsafe_control = json.loads(json.dumps(config))
            unsafe_control["transport_receipt"]["credentials_file"] = "/root/secret.json"
            with self.assertRaisesRegex(renderer.SourceBootstrapReceiveRenderError, "credential or payload"):
                renderer._assert_control_only_remote_config(unsafe_control)
            unsafe_payload = json.loads(json.dumps(config))
            unsafe_payload["preparation_receipt"]["status"] = b"archive-bytes-must-never-enter-ssh-config"
            with self.assertRaisesRegex(renderer.SourceBootstrapReceiveRenderError, "binary payload"):
                renderer._assert_control_only_remote_config(unsafe_payload)
            self.assertIn("provider-side encryption is disallowed", program)
            self.assertIn("controller envelope signature verification failed", program)
            self.assertIn('X-Amz-SignedHeaders"] != ["host"]', program)
            self.assertIn("if os.geteuid() != 0:", program)
            self.assertNotIn("bootstrap-publish-receipt-v1", program)
            source = SCRIPT.read_text(encoding="utf-8")
            self.assertIn("_load_exact_sibling", source)
            self.assertNotIn("import manage_webapp_fi_source_transport", source)

    def test_render_rejects_generic_route_drift_and_unsigned_envelope_drift(self):
        with tempfile.TemporaryDirectory(prefix="fi-bootstrap-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            value = json.loads(Path(fixture["publish"]).read_bytes())
            value["object_id"] = "different-package"
            unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
            value["receipt_sha256"] = digest(renderer.canonical_json_bytes(unsigned))
            write_private(Path(fixture["publish"]), canonical(value))
            with mock.patch.object(renderer, "_utc_now", return_value=NOW), self.assertRaisesRegex(
                renderer.SourceBootstrapReceiveRenderError, "generic source transport publish receipt is unsafe"
            ):
                self._render(fixture)

            fixture = self._fixture(Path(temporary) / "second")
            envelope = json.loads(Path(fixture["envelope"]).read_bytes())
            envelope["fi_bootstrap_recipient"] = CONTROLLER_RECIPIENT
            write_private(Path(fixture["envelope"]), canonical(envelope))
            with mock.patch.object(renderer, "_utc_now", return_value=NOW), self.assertRaisesRegex(
                renderer.SourceBootstrapReceiveRenderError, "delivery envelope is unsafe"
            ):
                self._render(fixture)

    def test_short_lived_sigv4_and_sigv2_urls_are_required(self):
        with tempfile.TemporaryDirectory(prefix="fi-bootstrap-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            with mock.patch.object(renderer, "_utc_now", return_value=NOW):
                self._render(fixture)
                expired = str(fixture["url"]).replace("X-Amz-Date=20260730T120000Z", "X-Amz-Date=20260730T110000Z")
                with self.assertRaisesRegex(renderer.SourceBootstrapReceiveRenderError, "expiry"):
                    self._render(fixture, url=expired)
                excessive = str(fixture["url"]).replace("X-Amz-Expires=300", "X-Amz-Expires=301")
                with self.assertRaisesRegex(renderer.SourceBootstrapReceiveRenderError, "expiry"):
                    self._render(fixture, url=excessive)
                object_value = fixture["published"]["object"]
                sigv2 = (
                    f"https://{HOST}/{BUCKET}/{object_value['object_key']}?versionId=version-001"
                    f"&AWSAccessKeyId=key&Signature=signature&Expires={int(NOW.timestamp()) + 300}"
                )
                self._render(fixture, url=sigv2)
                too_long_v2 = sigv2.replace(f"Expires={int(NOW.timestamp()) + 300}", f"Expires={int(NOW.timestamp()) + 301}")
                with self.assertRaisesRegex(renderer.SourceBootstrapReceiveRenderError, "expiry"):
                    self._render(fixture, url=too_long_v2)

    def _make_fake_executables(
        self,
        root: Path,
        *,
        archive: Path,
        object_value: dict[str, object],
        sse: bool = False,
        wrong_version: bool = False,
    ) -> tuple[Path, Path]:
        curl = root / "fake-curl"
        age = root / "fake-age"
        version = "wrong-version" if wrong_version else object_value["version_id"]
        sse_header = "x-amz-server-side-encryption: AES256\\r\\n" if sse else ""
        curl.write_text(
            "#!/bin/sh\n"
            "out=''\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = '--output' ]; then out=\"$2\"; shift 2; continue; fi\n"
            "  shift\n"
            "done\n"
            f"cp {shlex.quote(str(archive))} \"$out\" || exit 12\n"
            "printf 'HTTP/1.1 200 OK\\r\\n'\n"
            f"printf 'x-amz-version-id: {version}\\r\\n'\n"
            f"printf 'x-amz-meta-transport-schema: {renderer.transport.TRANSPORT_SCHEMA}\\r\\n'\n"
            "printf 'x-amz-meta-encryption: age-v1\\r\\n'\n"
            f"printf 'x-amz-meta-ciphertext-sha256: {object_value['ciphertext_sha256']}\\r\\n'\n"
            "printf 'x-amz-meta-recipient-mode: single\\r\\n'\n"
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

    def _execute_remote(self, program: str, config: dict[str, object], url: str) -> tuple[int, str]:
        encoded = base64.b64encode(renderer.canonical_json_bytes(config)).decode("ascii")
        stdout = io.StringIO()
        namespace = {"__name__": "__main__"}
        with mock.patch.object(sys, "argv", ["receiver", "program", encoded, "--", url]), contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as exited:
                exec(compile(program, "<test-webapp-fi-bootstrap-receiver>", "exec"), namespace)
        return int(exited.exception.code), stdout.getvalue()

    def _execution_inputs(
        self,
        *,
        sse: bool = False,
        wrong_version: bool = False,
    ) -> tuple[tempfile.TemporaryDirectory[str], dict[str, object], str, Path, str]:
        temporary = tempfile.TemporaryDirectory(prefix="fi-bootstrap-receiver-")
        root = Path(temporary.name)
        current = dt.datetime.now(dt.timezone.utc)
        fixture = self._fixture(root / "fixture", now=current)
        with mock.patch.object(renderer, "_utc_now", return_value=current):
            command = self._render(fixture)
        _inner, program, config = self._remote(command)
        receiver_root = root / "receiver-root"
        receiver_root.mkdir(mode=0o700)
        identity_root = root / "identities"
        identity = identity_root / str(config["campaign_id"]) / "webapp-fi" / "bootstrap.agekey"
        for directory in (identity_root, identity_root / str(config["campaign_id"]), identity.parent):
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)
        identity.write_text("AGE-SECRET-KEY-1TEST\n", encoding="ascii")
        identity.chmod(0o600)
        curl, age = self._make_fake_executables(
            root,
            archive=Path(fixture["archive"]),
            object_value=config["transport_receipt"]["object"],
            sse=sse,
            wrong_version=wrong_version,
        )
        program = (
            program.replace(renderer.DEFAULT_RECEIVER_ROOT, str(receiver_root))
            .replace(renderer.FI_CAMPAIGN_IDENTITY_ROOT, str(identity_root))
            .replace("/usr/bin/curl", str(curl))
            .replace("/usr/bin/age", str(age))
        )
        config["receiver_root"] = str(receiver_root)
        config["age_identity_file"] = str(identity)
        return temporary, config, str(fixture["url"]), receiver_root, program

    def test_embedded_receiver_executes_direct_s3_age_and_actual_installer_path_without_url_persistence(self):
        temporary, config, url, receiver_root, program = self._execution_inputs()
        try:
            result, output = self._execute_remote(program, config, url)
            self.assertEqual(result, 0, output)
            self.assertIn('"status": "installed"', output)
            installed = receiver_root / ("installed-" + config["tooling"]["control_commit"] + "-" + config["package_id"])
            install_receipt_path = installed / "source-adoption-install-receipt.json"
            self.assertTrue(install_receipt_path.is_file())
            returned = json.loads(output)
            self.assertEqual({"status", "install_receipt"}, set(returned))
            self.assertEqual("installed", returned["status"])
            self.assertEqual(json.loads(install_receipt_path.read_text(encoding="ascii")), returned["install_receipt"])
            returned_payload = json.dumps(returned, sort_keys=True).encode("ascii")
            self.assertNotIn(url.encode("utf-8"), returned_payload)
            self.assertNotIn(b"private_key", returned_payload)
            receiver_candidates = [path for path in receiver_root.iterdir() if path.name.startswith("receive-")]
            self.assertEqual(len(receiver_candidates), 1)
            self.assertTrue((receiver_candidates[0] / "control" / "delivery.json").is_file())
            self.assertTrue((receiver_candidates[0] / "control" / "delivery-envelope.json").is_file())
            self.assertTrue((receiver_candidates[0] / "control" / "preparation.json").is_file())
            for path in receiver_root.rglob("*"):
                if path.is_file():
                    payload = path.read_bytes()
                    self.assertNotIn(url.encode("utf-8"), payload, path)
                    self.assertNotIn(b"https://", payload, path)
        finally:
            temporary.cleanup()

    def test_embedded_receiver_rejects_sse_and_wrong_version_before_installer(self):
        for name, options in (("sse", {"sse": True}), ("version", {"wrong_version": True})):
            with self.subTest(case=name):
                temporary, config, url, receiver_root, program = self._execution_inputs(**options)
                try:
                    result, output = self._execute_remote(program, config, url)
                    self.assertEqual(result, 2, output)
                    self.assertIn('"status": "blocked"', output)
                    self.assertFalse(
                        (receiver_root / ("installed-" + config["tooling"]["control_commit"] + "-" + config["package_id"])).exists()
                    )
                finally:
                    temporary.cleanup()

    def test_embedded_receiver_rejects_read_only_or_insufficient_staging_before_download(self):
        for name, state in (
            ("read_only", type("Statvfs", (), {"f_bavail": 1024 * 1024, "f_frsize": 4096, "f_flag": getattr(os, "ST_RDONLY", 1)})()),
            ("insufficient", type("Statvfs", (), {"f_bavail": 0, "f_frsize": 4096, "f_flag": 0})()),
        ):
            with self.subTest(case=name):
                temporary, config, url, receiver_root, program = self._execution_inputs()
                try:
                    with mock.patch.object(os, "statvfs", return_value=state):
                        result, output = self._execute_remote(program, config, url)
                    self.assertEqual(result, 2, output)
                    self.assertIn('"status": "blocked"', output)
                    # The capacity guard precedes both candidate creation and
                    # the direct Object Storage curl invocation.
                    self.assertEqual([], list(receiver_root.iterdir()))
                finally:
                    temporary.cleanup()

    def test_embedded_receiver_rejects_writable_staging_parent_before_download(self):
        temporary, config, url, receiver_root, program = self._execution_inputs()
        try:
            receiver_root.parent.chmod(0o777)
            result, output = self._execute_remote(program, config, url)
            self.assertEqual(result, 2, output)
            self.assertIn('"status": "blocked"', output)
            self.assertEqual([], list(receiver_root.iterdir()))
        finally:
            temporary.cleanup()

    def test_embedded_receiver_rejects_legacy_or_cross_campaign_identity_before_download(self):
        for name in ("legacy", "cross_campaign"):
            with self.subTest(case=name):
                temporary, config, url, receiver_root, program = self._execution_inputs()
                try:
                    if name == "legacy":
                        config["age_identity_file"] = "/etc/trading-bot-three-site/webapp-fi/source-adoption-bootstrap.agekey"
                    else:
                        identity = Path(config["age_identity_file"])
                        config["age_identity_file"] = str(
                            identity.parents[2] / "other-campaign-20260730" / "webapp-fi" / "bootstrap.agekey"
                        )
                    result, output = self._execute_remote(program, config, url)
                    self.assertEqual(result, 2, output)
                    self.assertIn('"status": "blocked"', output)
                    self.assertEqual(list(receiver_root.iterdir()), [])
                finally:
                    temporary.cleanup()


class RenderWebAppFiSourceBootstrapLoaderTests(unittest.TestCase):
    def _fixture_code_file(self, root: Path, name: str, payload: str = "# fixture\n") -> Path:
        path = root / name
        path.write_text(payload, encoding="utf-8")
        path.chmod(0o644)
        return path

    def test_exact_sibling_loader_rejects_writable_sibling_and_unsafe_leaf(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fi-bootstrap-loader-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            source = self._fixture_code_file(root, "renderer.py")
            sibling = self._fixture_code_file(root, "writable-sibling.py")
            sibling.chmod(0o666)
            module_name = "_fi_bootstrap_writable_sibling_fixture"
            with mock.patch.object(renderer, "__file__", str(source)):
                with self.assertRaisesRegex(RuntimeError, "root-owned non-writable regular non-symlink"):
                    renderer._load_exact_sibling(sibling.name, module_name)
                with self.assertRaisesRegex(RuntimeError, "safe leaf name"):
                    renderer._load_exact_sibling("../writable-sibling.py", module_name)
            self.assertNotIn(module_name, sys.modules)

    def test_exact_sibling_loader_rejects_symlink_and_writable_ancestor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fi-bootstrap-loader-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            source = self._fixture_code_file(root, "renderer.py")
            target = self._fixture_code_file(root, "target.py", "raise AssertionError('must not execute')\n")
            sibling = root / "linked-sibling.py"
            sibling.symlink_to(target.name)
            linked_name = "_fi_bootstrap_symlink_sibling_fixture"
            with mock.patch.object(renderer, "__file__", str(source)):
                with self.assertRaisesRegex(RuntimeError, "root-owned non-writable regular non-symlink"):
                    renderer._load_exact_sibling(sibling.name, linked_name)
            self.assertNotIn(linked_name, sys.modules)

            unsafe_parent = root / "unsafe-parent"
            unsafe_parent.mkdir(mode=0o777)
            unsafe_parent.chmod(0o777)
            unsafe_source = self._fixture_code_file(unsafe_parent, "renderer.py")
            unsafe_sibling = self._fixture_code_file(unsafe_parent, "sibling.py")
            ancestor_name = "_fi_bootstrap_writable_ancestor_fixture"
            with mock.patch.object(renderer, "__file__", str(unsafe_source)):
                with self.assertRaisesRegex(RuntimeError, "parent is not root-controlled"):
                    renderer._load_exact_sibling(unsafe_sibling.name, ancestor_name)
            self.assertNotIn(ancestor_name, sys.modules)

    def test_exact_sibling_loader_restores_prior_module_after_import_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fi-bootstrap-loader-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            source = self._fixture_code_file(root, "renderer.py")
            sibling = self._fixture_code_file(root, "broken.py", "raise RuntimeError('fixture import failure')\n")
            module_name = "_fi_bootstrap_failed_import_fixture"
            previous = object()
            original = sys.modules.get(module_name)
            sys.modules[module_name] = previous
            try:
                with mock.patch.object(renderer, "__file__", str(source)):
                    with self.assertRaisesRegex(RuntimeError, "fixture import failure"):
                        renderer._load_exact_sibling(sibling.name, module_name)
                self.assertIs(previous, sys.modules.get(module_name))
            finally:
                if original is None:
                    sys.modules.pop(module_name, None)
                else:
                    sys.modules[module_name] = original


if __name__ == "__main__":
    unittest.main()
