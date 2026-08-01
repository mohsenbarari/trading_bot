import base64
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import tarfile
import tempfile
import unittest
import urllib.parse
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/render_webapp_ir_stage_bootstrap_receive.py"
SPEC = importlib.util.spec_from_file_location("render_webapp_ir_stage_bootstrap_receive", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
receiver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receiver)


def canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def write_archive(path: Path, members: dict[str, bytes], *, symlink_name: str | None = None) -> None:
    with path.open("wb") as stream:
        with tarfile.open(fileobj=stream, mode="w:", format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(members):
                entry = tarfile.TarInfo(name)
                entry.size = len(members[name])
                entry.mode = 0o600
                entry.uid = 0
                entry.gid = 0
                entry.mtime = 0
                archive.addfile(entry, io.BytesIO(members[name]))
            if symlink_name is not None:
                entry = tarfile.TarInfo(symlink_name)
                entry.type = tarfile.SYMTYPE
                entry.linkname = "target"
                entry.mode = 0o600
                entry.uid = 0
                entry.gid = 0
                entry.mtime = 0
                archive.addfile(entry)
    path.chmod(0o600)


class FakeRemoteResponse:
    def __init__(self, payload: bytes, *, url: str, headers: dict[str, str]) -> None:
        self._payload = payload
        self._offset = 0
        self._url = url
        self.headers = headers
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._payload) - self._offset
        result = self._payload[self._offset : self._offset + size]
        self._offset += len(result)
        return result

    def getcode(self) -> int:
        return 200

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self.closed = True


class RenderWebAppIrStageBootstrapReceiveTests(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
        *,
        signature: str = "a" * 64,
        security_token: str | None = None,
    ) -> dict[str, object]:
        package = root / "package"
        package.mkdir(mode=0o700, parents=True)
        campaign_id = "wa-ir-standby-97265988-4b12-444e-abda-165573b2769f"
        control_commit = "a" * 40
        control_tree = "b" * 40
        bootstrap_id = "bootstrap-001"
        config_value: dict[str, object] = {
            "schema": receiver.CONSUMER_CONFIG_SCHEMA,
            "campaign_id": campaign_id,
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "three-site-private",
            "prefix": "campaign-current/artifacts",
            "age_binary": "/usr/bin/age",
            "age_identity_file": receiver.wa_ir_bootstrap_identity_file(campaign_id),
            "age_recipient": "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
            "workspace": "/srv/trading-bot-three-site-staging-data/workspace",
            "source_site": "webapp_fi",
            "source_signing_public_key_base64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "webapp_fi_source_attestation_public_key_base64": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",
            "webapp_fi_controller_authorization_public_key_base64": "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI=",
            "maximum_artifact_bytes": 21474836480,
        }
        config_raw = canonical(config_value)
        payload_members = {
            "scripts/manage_webapp_ir_artifact_stage.py": b"# stage consumer\n",
            "scripts/manage_webapp_ir_snapshot.py": b"# snapshot helper\n",
            "scripts/manage_webapp_ir_release_provenance.py": b"# provenance helper\n",
            "scripts/prepare_webapp_ir_artifact_bundle.py": b"# image archive verifier helper\n",
            "scripts/verify_webapp_fi_source_provenance.py": b"# pure source verifier helper\n",
            "scripts/install_webapp_ir_static_assets.py": b"# detached static installer helper\n",
            "core/standby_snapshot_capacity.py": b"# capacity helper\n",
            "scripts/webapp_ir_image_archive_contract.py": b"# image archive contract\n",
            "config/consumer.json": config_raw,
        }
        manifest = {
            "schema": receiver.PACKAGE_MANIFEST_SCHEMA,
            "status": "prepared",
            "control": {"commit": control_commit, "tree": control_tree},
            "files": {name: digest(value) for name, value in payload_members.items()},
            "consumer_config_sha256": digest(config_raw),
        }
        manifest_raw = canonical(manifest)
        members = {**payload_members, receiver.PACKAGE_MANIFEST_MEMBER: manifest_raw}
        archive = package / receiver.PACKAGE_ARCHIVE_NAME
        write_archive(archive, members)
        archive_raw = archive.read_bytes()
        preparation_unsigned: dict[str, object] = {
            "schema": receiver.PREPARATION_RECEIPT_SCHEMA,
            "status": "prepared",
            "package_directory": str(package),
            "control_commit": control_commit,
            "control_tree": control_tree,
            "bootstrap_archive": {"name": receiver.PACKAGE_ARCHIVE_NAME, "sha256": digest(archive_raw), "bytes": len(archive_raw)},
            "package_manifest": {"name": receiver.PACKAGE_MANIFEST_MEMBER, "sha256": digest(manifest_raw)},
            "consumer_config_sha256": digest(config_raw),
        }
        preparation = dict(preparation_unsigned)
        preparation["receipt_sha256"] = digest(receiver._canonical_json_bytes(preparation_unsigned))
        preparation_path = package / receiver.PREPARATION_RECEIPT_NAME
        preparation_raw = canonical(preparation)
        write_private(preparation_path, preparation_raw)
        object_key = receiver._expected_bootstrap_object_key(
            prefix=str(config_value["prefix"]), control_commit=control_commit, bootstrap_id=bootstrap_id
        )
        query = [
            ("versionId", "version-001"),
            ("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
            ("X-Amz-Credential", "FAKEACCESSKEY/20260730/ir-thr-at1/s3/aws4_request"),
            ("X-Amz-Date", "20260730T120000Z"),
            ("X-Amz-Expires", "300"),
            ("X-Amz-SignedHeaders", "host"),
            ("X-Amz-Signature", signature),
        ]
        if security_token is not None:
            query.append(("X-Amz-Security-Token", security_token))
        url = (
            "https://s3.ir-thr-at1.arvanstorage.ir/three-site-private/" + object_key + "?"
            + urllib.parse.urlencode(query, quote_via=urllib.parse.quote, safe="-_.~")
        )
        published = {
            "schema": receiver.BOOTSTRAP_PUBLISH_RECEIPT_SCHEMA,
            "status": "published",
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "control_commit": control_commit,
            "control_tree": control_tree,
            "bootstrap_id": bootstrap_id,
            "published_at": "2026-07-30T00:00:00Z",
            "bootstrap": {
                "object_key": object_key,
                "version_id": "version-001",
                "ciphertext_sha256": "c" * 64,
                "ciphertext_bytes": len(archive_raw) + 128,
                "plaintext_sha256": digest(archive_raw),
                "plaintext_bytes": len(archive_raw),
                "manifest_sha256": digest(manifest_raw),
                "preparation_receipt_sha256": digest(preparation_raw),
                "presigned_url": url,
            },
        }
        publish_path = root / "publish.json"
        write_private(publish_path, json.dumps(published, sort_keys=True).encode("utf-8"))
        known_hosts = root / "wa-ir.known_hosts"
        known_hosts.write_text(
            "95.38.164.29 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEZpeHR1cmVLbm93bkhvc3RLZXkxMjM0NTY=\n",
            encoding="ascii",
        )
        known_hosts.chmod(0o644)
        return {
            "package": package,
            "preparation": preparation_path,
            "publish": publish_path,
            "url": url,
            "published": published,
            "members": members,
            "archive": archive,
            "known_hosts": known_hosts,
        }

    def _render(self, fixture: dict[str, object]) -> str:
        return receiver.render_receive_command(
            publish_receipt=fixture["publish"],
            bootstrap_package_directory=fixture["package"],
            preparation_receipt=fixture["preparation"],
            bootstrap_root="/srv/trading-bot-three-site-staging-data/wa-ir-bootstrap",
            wa_ir_known_hosts=fixture["known_hosts"],
        )

    def _remote(self, command: str) -> tuple[list[str], dict[str, object], dict[str, object]]:
        outer = shlex.split(command)
        self.assertEqual(outer[:5], ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"])
        self.assertEqual(outer[5:9], ["-o", outer[6], "-o", "GlobalKnownHostsFile=/dev/null"])
        self.assertTrue(outer[6].startswith("UserKnownHostsFile=/"))
        self.assertEqual(outer[-2], receiver.REMOTE_HOST)
        inner = shlex.split(outer[-1])
        self.assertEqual(inner[:5], ["/usr/bin/python3", "-I", "-B", "-c", receiver.REMOTE_LAUNCHER])
        self.assertEqual(inner[7], "--")
        program = base64.b64decode(inner[5]).decode("utf-8")
        namespace: dict[str, object] = {"__name__": "receiver_test"}
        exec(compile(program, "<receiver-test>", "exec"), namespace)
        config = json.loads(base64.b64decode(inner[6]).decode("utf-8"))
        return inner, namespace, config

    def _valid_headers(self, config: dict[str, object]) -> bytes:
        return (
            "HTTP/1.1 200 OK\r\n"
            + "x-amz-version-id: " + str(config["version_id"]) + "\r\n"
            + "x-amz-meta-transport-schema: " + receiver.TRANSPORT_SCHEMA + "\r\n"
            + "x-amz-meta-encryption: " + receiver.OBJECT_ENCRYPTION + "\r\n"
            + "x-amz-meta-ciphertext-sha256: " + str(config["ciphertext_sha256"]) + "\r\n"
            + "content-length: " + str(config["ciphertext_bytes"]) + "\r\n\r\n"
        ).encode("ascii")

    @staticmethod
    def _rewrite_query(url: str, mutate: object) -> str:
        parsed = urllib.parse.urlsplit(url)
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        mutate(pairs)  # type: ignore[operator]
        return urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote, safe="-_.~"),
                parsed.fragment,
            )
        )

    def test_valid_root_only_package_renders_one_safely_quoted_emit_only_command(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            command = self._render(fixture)
            inner, namespace, config = self._remote(command)
            self.assertEqual(inner[-1], fixture["url"])
            self.assertEqual(inner.count(fixture["url"]), 1)
            self.assertNotIn("subprocess", receiver.__dict__)
            self.assertNotIn(fixture["url"], base64.b64decode(inner[5]).decode("utf-8"))
            self.assertNotIn(fixture["url"], base64.b64decode(inner[6]).decode("utf-8"))
            self.assertNotIn("presigned_url", config)
            self.assertNotIn("presigned_url", base64.b64decode(inner[5]).decode("utf-8"))
            self.assertNotIn("StrictHostKeyChecking=accept-new", command)
            self.assertNotIn("BatchMode=no", command)
            self.assertEqual(namespace["load_config"](inner[6]), config)
            namespace["validate_url"](fixture["url"], config)
            namespace["validate_headers"](self._valid_headers(config), config)

    def test_just_published_receipt_can_be_consumed_from_stdin_without_a_durable_file(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            payload = fixture["publish"].read_bytes()
            fixture["publish"].unlink()
            command = receiver.render_receive_command(
                publish_receipt_bytes=payload,
                bootstrap_package_directory=fixture["package"],
                preparation_receipt=fixture["preparation"],
                bootstrap_root="/srv/trading-bot-three-site-staging-data/wa-ir-bootstrap",
                wa_ir_known_hosts=fixture["known_hosts"],
            )
            inner, _, _ = self._remote(command)
            self.assertEqual(inner[-1], fixture["url"])
            self.assertEqual(inner.count(fixture["url"]), 1)

    def test_direct_cli_blocks_before_reading_the_url_bearing_receipt(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            url = fixture["url"]
            stdin = io.TextIOWrapper(io.BytesIO(json.dumps(fixture["published"]).encode("utf-8")), encoding="utf-8")
            stdout = io.StringIO()
            with (
                mock.patch.object(receiver.sys, "stdin", stdin),
                mock.patch.object(
                    receiver,
                    "_read_publish_receipt_stdin",
                    side_effect=AssertionError("direct CLI must not read the URL-bearing receipt"),
                ),
                mock.patch.object(
                    receiver,
                    "render_receive_command",
                    side_effect=AssertionError("direct CLI must not render a disposable command"),
                ),
                contextlib.redirect_stdout(stdout),
            ):
                result = receiver.main([
                    "--publish-receipt-stdin",
                    "--bootstrap-package-directory", str(fixture["package"]),
                    "--preparation-receipt", str(fixture["preparation"]),
                    "--bootstrap-root", "/srv/trading-bot-three-site-staging-data/wa-ir-bootstrap",
                    "--wa-ir-known-hosts", str(fixture["known_hosts"]),
                ])
            self.assertEqual(result, 2)
            emitted = stdout.getvalue().strip()
            self.assertEqual("blocked", json.loads(emitted)["status"])
            self.assertIn("not rendered", emitted)
            self.assertNotIn(str(url), emitted)
            self.assertNotIn("ssh ", emitted)

    def test_direct_cli_parse_rejection_does_not_reflect_a_url_to_stdout_or_stderr(self):
        url = "https://s3.ir-thr-at1.arvanstorage.ir/private/object?X-Amz-Signature=" + "a" * 64
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(2, receiver.main(["--bootstrap-root", url]))
        self.assertNotIn(url, stdout.getvalue())
        self.assertNotIn(url, stderr.getvalue())

    def test_rejects_ambiguous_or_oversized_publish_receipt_sources(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            shared = {
                "bootstrap_package_directory": fixture["package"],
                "preparation_receipt": fixture["preparation"],
                "bootstrap_root": "/srv/trading-bot-three-site-staging-data/wa-ir-bootstrap",
                "wa_ir_known_hosts": fixture["known_hosts"],
            }
            with self.assertRaisesRegex(receiver.BootstrapReceiveRenderError, "exactly one"):
                receiver.render_receive_command(**shared)
            with self.assertRaisesRegex(receiver.BootstrapReceiveRenderError, "exactly one"):
                receiver.render_receive_command(
                    publish_receipt=fixture["publish"],
                    publish_receipt_bytes=fixture["publish"].read_bytes(),
                    **shared,
                )
            with self.assertRaisesRegex(receiver.BootstrapReceiveRenderError, "fixed size bound"):
                receiver.render_receive_command(
                    publish_receipt_bytes=b"x" * (receiver.MAX_CONTROL_FILE_BYTES + 1),
                    **shared,
                )

    def test_url_shell_metacharacters_remain_one_final_remote_argument(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary), security_token="x'$(id);safe")
            command = self._render(fixture)
            inner, _, _ = self._remote(command)
            self.assertEqual(inner[-1], fixture["url"])
            self.assertEqual(len(inner), 9)
            self.assertEqual(inner[4], receiver.REMOTE_LAUNCHER)

    def test_rejects_a_bootstrap_root_the_provenance_installer_cannot_read(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            with self.assertRaisesRegex(
                receiver.BootstrapReceiveRenderError,
                "incompatible with the provenance installer",
            ):
                receiver.render_receive_command(
                    publish_receipt=fixture["publish"],
                    bootstrap_package_directory=fixture["package"],
                    preparation_receipt=fixture["preparation"],
                    bootstrap_root="/srv/wa-ir+bootstrap",
                    wa_ir_known_hosts=fixture["known_hosts"],
                )

    def test_rejects_non_https_versionless_or_unbound_publish_url(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            for mutation in (
                lambda url: url.replace("https://", "http://", 1),
                lambda url: url.replace("versionId=version-001&", "", 1),
                lambda url: url.replace("s3.ir-thr-at1.arvanstorage.ir", "wrong.example.invalid", 1),
                lambda url: url + "&versionId=version-001",
                lambda url: url + "&malformed",
            ):
                published = dict(fixture["published"])
                bootstrap = dict(published["bootstrap"])
                bootstrap["presigned_url"] = mutation(str(bootstrap["presigned_url"]))
                published["bootstrap"] = bootstrap
                write_private(fixture["publish"], json.dumps(published, sort_keys=True).encode("utf-8"))
                with self.assertRaises(receiver.BootstrapReceiveRenderError):
                    self._render(fixture)

    def test_bootstrap_url_validators_require_sigv4_scope_and_reject_literal_null_versions(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            _, namespace, config = self._remote(self._render(fixture))
            published = fixture["published"]
            bootstrap = published["bootstrap"]
            valid = str(fixture["url"])

            def validate_outer(url: str) -> None:
                receiver._validate_presigned_url(
                    url,
                    endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                    bucket="three-site-private",
                    object_key=bootstrap["object_key"],
                    version_id="version-001",
                )

            validate_outer(valid)
            namespace["validate_url"](valid, config)
            mutations = (
                lambda pairs: pairs.__delitem__(next(index for index, pair in enumerate(pairs) if pair[0] == "X-Amz-Date")),
                lambda pairs: pairs.__setitem__(next(index for index, pair in enumerate(pairs) if pair[0] == "X-Amz-Expires"), ("X-Amz-Expires", "901")),
                lambda pairs: pairs.__setitem__(next(index for index, pair in enumerate(pairs) if pair[0] == "X-Amz-Credential"), ("X-Amz-Credential", "FAKEACCESSKEY/20260730/us-east-1/s3/aws4_request")),
                lambda pairs: pairs.append(("unexpected", "value")),
                lambda pairs: pairs.append(("versionId", "version-001")),
                lambda pairs: pairs.extend((("AWSAccessKeyId", "legacy"), ("Signature", "legacy"), ("Expires", "1"))),
            )
            for mutate in mutations:
                with self.subTest(mutate=mutate):
                    mutated = self._rewrite_query(valid, mutate)
                    with self.assertRaises(receiver.BootstrapReceiveRenderError):
                        validate_outer(mutated)
                    with self.assertRaises(namespace["ReceiveError"]):
                        namespace["validate_url"](mutated, config)
            with self.assertRaises(receiver.BootstrapReceiveRenderError):
                receiver._validate_presigned_url(
                    valid,
                    endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                    bucket="three-site-private",
                    object_key=bootstrap["object_key"],
                    version_id="null",
                )
            null_config = dict(config)
            null_config["version_id"] = "null"
            with self.assertRaises(namespace["ReceiveError"]):
                namespace["validate_url"](valid, null_config)

    def test_rejects_publish_or_preparation_binding_mismatches(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            published = dict(fixture["published"])
            bootstrap = dict(published["bootstrap"])
            bootstrap["preparation_receipt_sha256"] = "0" * 64
            published["bootstrap"] = bootstrap
            write_private(fixture["publish"], json.dumps(published).encode("utf-8"))
            with self.assertRaisesRegex(receiver.BootstrapReceiveRenderError, "preparation receipt hash"):
                self._render(fixture)
            fixture = self._fixture(Path(temporary) / "second")
            fixture["archive"].write_bytes(fixture["archive"].read_bytes() + b"tamper")
            fixture["archive"].chmod(0o600)
            with self.assertRaisesRegex(receiver.BootstrapReceiveRenderError, "does not match"):
                self._render(fixture)

    def test_rejects_legacy_or_cross_campaign_identity_path_and_non_private_inputs(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            config_member = json.loads(fixture["members"]["config/consumer.json"])
            config_member["age_identity_file"] = "/etc/trading-bot-three-site/wa-ir/artifact-stage-2c08.agekey"
            # Rebuild a fully self-consistent package; the pinned identity remains the only rejection.
            fixture = self._fixture(root / "wrong")
            config_member = json.loads(fixture["members"]["config/consumer.json"])
            config_member["age_identity_file"] = receiver.wa_ir_bootstrap_identity_file(
                "wa-ir-standby-11111111-2222-3333-4444-555555555555"
            )
            with self.assertRaisesRegex(receiver.BootstrapReceiveRenderError, "campaign WA-IR bootstrap age identity"):
                receiver._validate_consumer_config(canonical(config_member))
            config_member = json.loads(fixture["members"]["config/consumer.json"])
            config_member["webapp_fi_controller_authorization_public_key_base64"] = "AQ=="
            with self.assertRaisesRegex(receiver.BootstrapReceiveRenderError, "controller authorization"):
                receiver._validate_consumer_config(canonical(config_member))
            fixture["publish"].chmod(0o644)
            with self.assertRaisesRegex(receiver.BootstrapReceiveRenderError, "unsafe ownership"):
                self._render(fixture)

    def test_rejects_a_known_hosts_pin_without_the_exact_wa_ir_host(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            fixture["known_hosts"].write_text(
                "95.38.164.30 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEZpeHR1cmVLbm93bkhvc3RLZXkxMjM0NTY=\n",
                encoding="ascii",
            )
            fixture["known_hosts"].chmod(0o644)
            with self.assertRaisesRegex(receiver.BootstrapReceiveRenderError, "known_hosts lacks the exact WA-IR host key"):
                self._render(fixture)

    def test_remote_url_and_header_validation_rejects_redirects_and_mismatches(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            _, namespace, config = self._remote(self._render(fixture))
            for url in (
                str(fixture["url"]).replace("https://", "http://", 1),
                str(fixture["url"]).replace("versionId=version-001&", "", 1),
                str(fixture["url"]).replace("s3.ir-thr-at1.arvanstorage.ir", "elsewhere.invalid", 1),
                str(fixture["url"]) + "&versionId=version-001",
            ):
                with self.assertRaises(namespace["ReceiveError"]):
                    namespace["validate_url"](url, config)
            valid = self._valid_headers(config)
            namespace["validate_headers"](valid, config)
            bad_headers = (
                b"HTTP/1.1 302 Found\r\nLocation: https://elsewhere.invalid/\r\n\r\n",
                valid.replace(b"x-amz-version-id: version-001", b"x-amz-version-id: other"),
                valid.replace(b"content-length: ", b"x-amz-server-side-encryption: AES256\r\ncontent-length: "),
                valid.replace(b"content-length: ", b"x-amz-server-side-encryption-aws-kms-key-id: key\r\ncontent-length: "),
                valid.replace(b"content-length: ", b"x-amz-server-side-encryption-customer-algorithm: AES256\r\ncontent-length: "),
                valid.replace(b"content-length: ", b"x-amz-kms-key-id: key\r\ncontent-length: "),
                valid.replace(str(config["ciphertext_bytes"]).encode("ascii"), b"1", 1),
            )
            for headers in bad_headers:
                with self.assertRaises(namespace["ReceiveError"]):
                    namespace["validate_headers"](headers, config)

    def test_remote_bootstrap_download_enforces_exact_stream_bound_and_removes_output(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            _, namespace, config = self._remote(self._render(fixture))
            config = dict(config)
            config["ciphertext_bytes"] = 4
            config["ciphertext_sha256"] = digest(b"abcd")
            headers = {
                "x-amz-version-id": str(config["version_id"]),
                "x-amz-meta-transport-schema": receiver.TRANSPORT_SCHEMA,
                "x-amz-meta-encryption": receiver.OBJECT_ENCRYPTION,
                "x-amz-meta-ciphertext-sha256": str(config["ciphertext_sha256"]),
            }
            for payload in (b"abc", b"abcde"):
                with self.subTest(payload=payload):
                    output = root / ("ciphertext-" + str(len(payload)) + ".age")
                    response = FakeRemoteResponse(payload, url=str(fixture["url"]), headers=headers)
                    namespace["open_presigned_request"] = lambda _request, _timeout: response
                    with self.assertRaises(namespace["ReceiveError"]):
                        namespace["download_exact_ciphertext"](str(fixture["url"]), config, output)
                    self.assertFalse(output.exists())
                    self.assertTrue(response.closed)

    def test_remote_receive_failure_preserves_its_fresh_candidate_and_removes_partial_ciphertext(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            _, namespace, config = self._remote(self._render(fixture))
            bootstrap_root = root / "bootstrap-root"
            bootstrap_root.mkdir(mode=0o700)
            identity = root / "identity.agekey"
            write_private(identity, b"test identity\n")
            config = dict(config)
            config["bootstrap_root"] = str(bootstrap_root)
            config["age_identity_file"] = str(identity)
            candidate = bootstrap_root / (
                "received-" + str(config["control_commit"]) + "-" + str(config["bootstrap_id"])
            )

            def failed_download(_url, _config, output):
                write_private(output, b"partial bootstrap ciphertext")
                output.unlink()
                raise namespace["ReceiveError"]("download failed")

            namespace["validate_url"] = lambda _url, _config: None
            namespace["require_trusted_executable"] = lambda path: path
            namespace["require_root_private_file"] = lambda _path: identity
            namespace["require_root_private_directory"] = lambda _path: bootstrap_root
            namespace["download_exact_ciphertext"] = failed_download

            with self.assertRaises(namespace["ReceiveError"]):
                namespace["receive"](config, str(fixture["url"]))

            self.assertTrue(candidate.is_dir())
            self.assertFalse((candidate / ".ciphertext").exists())
            self.assertFalse((candidate / namespace["RECEIPT_NAME"]).exists())

    def test_remote_archive_verifier_accepts_exact_archive_and_rejects_unsafe_members(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            root = Path(temporary)
            fixture = self._fixture(root)
            _, namespace, config = self._remote(self._render(fixture))
            observed = namespace["verify_archive"](fixture["archive"], config)
            self.assertEqual(set(observed), set(fixture["members"]))
            unsafe_sets = []
            unexpected = dict(fixture["members"])
            unexpected.pop("config/consumer.json")
            unexpected["unexpected.txt"] = b"unexpected\n"
            unsafe_sets.append((unexpected, None))
            traversal = dict(fixture["members"])
            traversal.pop("config/consumer.json")
            traversal["../escape"] = b"escape\n"
            unsafe_sets.append((traversal, None))
            for index, (members, symlink) in enumerate(unsafe_sets):
                path = root / f"unsafe-{index}.tar"
                write_archive(path, members, symlink_name=symlink)
                with self.assertRaises(namespace["ReceiveError"]):
                    namespace["verify_archive"](path, config)
            symlink = root / "symlink.tar"
            symlink_members = dict(fixture["members"])
            symlink_members.pop("config/consumer.json")
            write_archive(symlink, symlink_members, symlink_name="config/consumer.json")
            with self.assertRaises(namespace["ReceiveError"]):
                namespace["verify_archive"](symlink, config)

    def test_remote_receipt_writer_refuses_url_persistence(self):
        with tempfile.TemporaryDirectory(prefix="wa-ir-render-") as temporary:
            fixture = self._fixture(Path(temporary))
            _, namespace, config = self._remote(self._render(fixture))
            receipt = namespace["build_receipt"](
                config,
                "/srv/trading-bot-three-site-staging-data/wa-ir-bootstrap/candidate",
                "2026-07-30T00:00:00Z",
            )
            self.assertEqual(receipt["schema"], "gold-trade-wa-ir-stage-bootstrap-receipt-v1")
            self.assertEqual(
                set(receipt),
                {
                    "schema", "status", "received_at", "source_site", "destination_site", "control_commit",
                    "control_tree", "bootstrap_id", "candidate_directory", "files", "bootstrap", "receipt_sha256",
                },
            )
            self.assertEqual(
                set(receipt["bootstrap"]),
                {
                    "object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes", "plaintext_sha256",
                    "plaintext_bytes", "package_manifest_sha256", "preparation_receipt_sha256",
                    "consumer_config_sha256",
                },
            )
            verifier = "scripts/verify_webapp_fi_source_provenance.py"
            self.assertEqual(
                receipt["files"][verifier],
                digest(fixture["members"][verifier]),
            )
            self.assertNotIn(fixture["url"], json.dumps(receipt, sort_keys=True))
            with self.assertRaises(namespace["ReceiveError"]):
                namespace["write_new_private_json"](Path(temporary) / "receipt.json", {"url": fixture["url"]})


if __name__ == "__main__":
    unittest.main()
