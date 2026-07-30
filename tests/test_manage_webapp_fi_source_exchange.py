"""Focused tests for the FI-only source exchange helper."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import quote


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "manage_webapp_fi_source_exchange.py"
SPEC = importlib.util.spec_from_file_location("manage_webapp_fi_source_exchange", MODULE_PATH)
assert SPEC and SPEC.loader
exchange = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = exchange
SPEC.loader.exec_module(exchange)
contract = exchange.contract


def recipient(character: str) -> str:
    return "age1" + character * 40


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.put_version = "put-version-01"
        self.get_version = "get-version-01"
        self.download_payload = b""
        self.fail_put = False
        self.malformed_put_headers = False
        self.put_sse: str | None = None

    def __call__(self, command: object) -> None:
        assert isinstance(command, (list, tuple))
        values = [str(item) for item in command]
        self.commands.append(values)
        if values[0] == "/usr/bin/age":
            output = Path(values[values.index("-o") + 1])
            source = Path(values[-1])
            if "-d" in values:
                opaque = source.read_bytes()
                _tag, _recipients, plaintext = opaque.split(b"\x00", 2)
                output.write_bytes(plaintext)
                return
            recipients = [values[index + 1] for index, value in enumerate(values) if value == "-r"]
            output.write_bytes(b"FAKE-AGE\x00" + "|".join(recipients).encode("ascii") + b"\x00" + source.read_bytes())
            return
        assert values[0] == exchange.CURL_BINARY
        headers_path = Path(values[values.index("--dump-header") + 1])
        method = values[values.index("--request") + 1]
        if method == "PUT":
            if self.fail_put:
                raise RuntimeError("ambiguous PUT failure")
            if self.malformed_put_headers:
                headers_path.write_bytes(b"HTTP/1.1 200 OK\r\n\r\n")
                return
            response = (
                b"HTTP/1.1 100 Continue\r\n\r\n"
                + b"HTTP/1.1 200 OK\r\n"
                + b"x-amz-version-id: "
                + self.put_version.encode("ascii")
                + b"\r\n"
            )
            if self.put_sse:
                response += b"x-amz-server-side-encryption: " + self.put_sse.encode("ascii") + b"\r\n"
            headers_path.write_bytes(response + b"\r\n")
            return
        assert method == "GET"
        output = Path(values[values.index("--output") + 1])
        output.write_bytes(self.download_payload)
        headers_path.write_bytes(
            b"HTTP/1.1 200 OK\r\n"
            + b"x-amz-version-id: "
            + self.get_version.encode("ascii")
            + b"\r\n\r\n"
        )


class WebAppFiSourceExchangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fi-source-exchange-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.policy_path = self.root / "policy.json"
        self._write_policy()
        self.policy = exchange.load_policy(self.policy_path)
        self.plaintext = self.root / "source.bin"
        self.plaintext.write_bytes(b"FI source payload\x00with opaque bytes")
        self.plaintext.chmod(0o600)
        self.identity = self.root / "fi.agekey"
        self.identity.write_bytes(b"# fake root-only age identity\n")
        self.identity.chmod(0o600)
        self.runner = FakeRunner()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_policy(self, **changes: object) -> None:
        policy: dict[str, object] = {
            "schema": contract.CONFIG_SCHEMA,
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "private-artifacts",
            "prefix": "campaigns/three-site",
            "age_binary": "/usr/bin/age",
            "workspace": str(self.workspace),
            "controller_age_recipient": recipient("a"),
            "webapp_fi_age_recipient": recipient("c"),
            "webapp_ir_age_recipient": recipient("d"),
            "maximum_plaintext_bytes": 1024 * 1024,
        }
        policy.update(changes)
        self.policy_path.write_bytes(canonical(policy))
        self.policy_path.chmod(0o600)

    def _request(self, **changes: object) -> object:
        values: dict[str, object] = {
            "campaign_id": "00000000-0000-4000-8000-000000000001",
            "release_sha": "4" * 40,
            "control_commit": "5" * 40,
            "control_tree": "6" * 40,
            "source_site": "webapp_fi",
            "destination_site": contract.STATIC_DESTINATION_SITE,
            "object_kind": contract.STATIC_OBJECT_KIND,
            "object_id": "static-20260730-01",
            "mode": contract.STATIC_MODE,
            "recipients": (
                self.policy.controller_age_recipient,
                self.policy.webapp_ir_age_recipient,
            ),
        }
        values.update(changes)
        return contract.SourceObjectRequest(**values)

    def _presigned_url(self, *, key: str, version_id: str | None = None, signed_headers: object | None = None) -> str:
        signed = signed_headers
        if signed is None:
            signed = "host" if version_id is not None else ";".join(
                (
                    "content-type",
                    "host",
                    "if-none-match",
                    "x-amz-meta-ciphertext-sha256",
                    "x-amz-meta-encryption",
                    "x-amz-meta-recipient-mode",
                    "x-amz-meta-transport-schema",
                )
            )
        query = [
            "X-Amz-Algorithm=AWS4-HMAC-SHA256",
            "X-Amz-Credential=fake%2F20260730%2Fir-thr-at1%2Fs3%2Faws4_request",
            "X-Amz-Date=20260730T010203Z",
            "X-Amz-Expires=300",
            "X-Amz-SignedHeaders=" + quote(str(signed), safe=""),
            "X-Amz-Signature=" + "a" * 64,
        ]
        if version_id is not None:
            query.insert(0, "versionId=" + quote(version_id, safe=""))
        return (
            "https://s3.ir-thr-at1.arvanstorage.ir/"
            + quote(self.policy.bucket, safe="")
            + "/"
            + quote(key, safe="/")
            + "?"
            + "&".join(query)
        )

    def _prepare_static(self, directory_name: str = "prepared-static") -> tuple[object, Path, dict[str, object]]:
        request = self._request()
        prepared = self.workspace / directory_name
        receipt = exchange.prepare_upload(
            policy=self.policy,
            request=request,
            plaintext_path=self.plaintext,
            prepared_dir=prepared,
            command_runner=self.runner,
        )
        return request, prepared, receipt

    def test_prepare_and_single_put_bind_exact_static_recipients_without_persisting_url(self) -> None:
        request, prepared, prepared_receipt = self._prepare_static()
        ciphertext = (prepared / exchange.PREPARED_CIPHERTEXT_NAME).read_bytes()
        expected_recipients = (self.policy.controller_age_recipient, self.policy.webapp_ir_age_recipient)
        self.assertIn(expected_recipients[0].encode("ascii"), ciphertext)
        self.assertIn(expected_recipients[1].encode("ascii"), ciphertext)
        self.assertFalse((prepared / "plaintext.snapshot").exists())
        self.assertEqual("prepared", prepared_receipt["status"])
        key = contract.source_object_key(self.policy, request)
        report = exchange.upload_prepared(
            policy=self.policy,
            prepared_dir=prepared,
            upload_url=self._presigned_url(key=key),
            command_runner=self.runner,
        )
        self.assertEqual(self.runner.put_version, report["object"]["version_id"])
        self.assertEqual(report, exchange.verify_upload_report(
            policy=self.policy,
            payload=(prepared / exchange.UPLOAD_REPORT_NAME).read_bytes(),
        ))
        self.assertTrue((prepared / exchange.UPLOAD_ATTEMPT_NAME).is_file())
        self.assertNotIn(b"://", (prepared / exchange.PREPARED_RECEIPT_NAME).read_bytes())
        self.assertNotIn(b"://", (prepared / exchange.UPLOAD_REPORT_NAME).read_bytes())
        self.assertFalse(any("docker" in " ".join(command).lower() for command in self.runner.commands))
        with self.assertRaisesRegex(exchange.SourceExchangeError, "PUT-attempt marker"):
            exchange.upload_prepared(
                policy=self.policy,
                prepared_dir=prepared,
                upload_url=self._presigned_url(key=key),
                command_runner=self.runner,
            )

    def test_upload_requires_sigv4_to_bind_every_create_only_metadata_header(self) -> None:
        request, prepared, _receipt = self._prepare_static()
        with self.assertRaisesRegex(exchange.SourceExchangeError, "transient FI upload URL or expectation is invalid"):
            exchange.upload_prepared(
                policy=self.policy,
                prepared_dir=prepared,
                upload_url=self._presigned_url(
                    key=contract.source_object_key(self.policy, request),
                    signed_headers="host",
                ),
                command_runner=self.runner,
            )
        self.assertFalse((prepared / exchange.UPLOAD_ATTEMPT_NAME).exists())
        self.assertFalse(any(command[0] == exchange.CURL_BINARY for command in self.runner.commands))

    def test_raw_image_and_source_evidence_are_pinned_to_controller_only(self) -> None:
        for object_kind, object_id in (
            (contract.RAW_APP_IMAGE_OBJECT_KIND, "raw-image-20260730-01"),
            (contract.SOURCE_EVIDENCE_OBJECT_KIND, "source-evidence-20260730-01"),
        ):
            with self.subTest(object_kind=object_kind):
                request = self._request(
                    destination_site="controller",
                    object_kind=object_kind,
                    object_id=object_id,
                    mode=contract.SINGLE_MODE,
                    recipients=(self.policy.controller_age_recipient,),
                )
                prepared = self.workspace / ("prepared-" + object_kind)
                exchange.prepare_upload(
                    policy=self.policy,
                    request=request,
                    plaintext_path=self.plaintext,
                    prepared_dir=prepared,
                    command_runner=self.runner,
                )
                ciphertext = (prepared / exchange.PREPARED_CIPHERTEXT_NAME).read_bytes()
                self.assertIn(self.policy.controller_age_recipient.encode("ascii"), ciphertext)
                self.assertNotIn(self.policy.webapp_ir_age_recipient.encode("ascii"), ciphertext)

    def test_generic_cli_rejects_post_packet_kinds_before_creating_a_prepared_directory(self) -> None:
        """Raw/evidence must use the strict packet-derived helper, not this CLI."""

        request = self._request(
            destination_site="controller",
            object_kind=contract.RAW_APP_IMAGE_OBJECT_KIND,
            object_id="raw-image-20260730-01",
            mode=contract.SINGLE_MODE,
            recipients=(self.policy.controller_age_recipient,),
        )
        request_path = self.root / "raw-request.json"
        request_path.write_bytes(canonical(exchange._request_to_value(request)))
        request_path.chmod(0o600)
        prepared = self.workspace / "blocked-raw"
        with mock.patch.object(exchange, "_print_result") as printed:
            result = exchange.main(
                [
                    "prepare-upload",
                    "--policy",
                    str(self.policy_path),
                    "--request",
                    str(request_path),
                    "--plaintext",
                    str(self.plaintext),
                    "--prepared-dir",
                    str(prepared),
                ]
            )
        self.assertEqual(2, result)
        self.assertFalse(prepared.exists())
        self.assertIn("initial dual-recipient static route", printed.call_args.args[0]["error"])

    def test_generic_cli_rejects_prepared_post_packet_upload_before_network(self) -> None:
        """A pre-existing raw-image receipt cannot bypass the strict helper."""

        request = self._request(
            destination_site="controller",
            object_kind=contract.RAW_APP_IMAGE_OBJECT_KIND,
            object_id="raw-image-20260730-02",
            mode=contract.SINGLE_MODE,
            recipients=(self.policy.controller_age_recipient,),
        )
        prepared = self.workspace / "prepared-raw-for-cli-rejection"
        exchange.prepare_upload(
            policy=self.policy,
            request=request,
            plaintext_path=self.plaintext,
            prepared_dir=prepared,
            command_runner=self.runner,
        )
        with (
            mock.patch.object(exchange, "_print_result") as printed,
            mock.patch.object(exchange, "upload_prepared") as upload,
        ):
            result = exchange.main(
                [
                    "upload-prepared",
                    "--policy",
                    str(self.policy_path),
                    "--prepared-dir",
                    str(prepared),
                    "--upload-url",
                    self._presigned_url(key=contract.source_object_key(self.policy, request)),
                ]
            )
        self.assertEqual(2, result)
        upload.assert_not_called()
        self.assertFalse((prepared / exchange.UPLOAD_ATTEMPT_NAME).exists())
        self.assertFalse((prepared / exchange.UPLOAD_REPORT_NAME).exists())
        self.assertFalse(any(command[0] == exchange.CURL_BINARY for command in self.runner.commands))
        self.assertIn("initial dual-recipient static route", printed.call_args.args[0]["error"])

    def test_ambiguous_put_leaves_marker_and_blocks_automatic_retry(self) -> None:
        request, prepared, _receipt = self._prepare_static()
        self.runner.fail_put = True
        with self.assertRaisesRegex(exchange.SourceExchangeError, "retry is intentionally blocked"):
            exchange.upload_prepared(
                policy=self.policy,
                prepared_dir=prepared,
                upload_url=self._presigned_url(key=contract.source_object_key(self.policy, request)),
                command_runner=self.runner,
            )
        self.assertTrue((prepared / exchange.UPLOAD_ATTEMPT_NAME).is_file())
        self.runner.fail_put = False
        with self.assertRaisesRegex(exchange.SourceExchangeError, "PUT-attempt marker"):
            exchange.upload_prepared(
                policy=self.policy,
                prepared_dir=prepared,
                upload_url=self._presigned_url(key=contract.source_object_key(self.policy, request)),
                command_runner=self.runner,
            )

    def test_post_put_response_parse_failure_keeps_durable_reconciliation_inputs(self) -> None:
        request, prepared, _receipt = self._prepare_static()
        self.runner.malformed_put_headers = True
        with self.assertRaisesRegex(exchange.SourceExchangeError, "lacks one exact VersionId"):
            exchange.upload_prepared(
                policy=self.policy,
                prepared_dir=prepared,
                upload_url=self._presigned_url(key=contract.source_object_key(self.policy, request)),
                command_runner=self.runner,
            )
        self.assertTrue((prepared / exchange.UPLOAD_ATTEMPT_NAME).is_file())
        self.assertTrue((prepared / exchange.PREPARED_CIPHERTEXT_NAME).is_file())
        self.assertTrue((prepared / exchange.PREPARED_RECEIPT_NAME).is_file())
        self.assertFalse((prepared / exchange.UPLOAD_REPORT_NAME).exists())

    def test_put_response_rejects_provider_side_sse_and_retains_attempt_marker(self) -> None:
        request, prepared, _receipt = self._prepare_static()
        self.runner.put_sse = "AES256"
        with self.assertRaisesRegex(exchange.SourceExchangeError, "forbidden provider-side encryption"):
            exchange.upload_prepared(
                policy=self.policy,
                prepared_dir=prepared,
                upload_url=self._presigned_url(key=contract.source_object_key(self.policy, request)),
                command_runner=self.runner,
            )
        self.assertTrue((prepared / exchange.UPLOAD_ATTEMPT_NAME).is_file())

    def test_receives_only_exact_version_bound_controller_static_provenance(self) -> None:
        request = self._request(
            source_site="controller",
            destination_site="webapp_fi",
            object_kind=contract.STATIC_PROVENANCE_OBJECT_KIND,
            object_id="provenance-20260730-01",
            mode=contract.SINGLE_MODE,
            recipients=(self.policy.webapp_fi_age_recipient,),
        )
        plaintext = canonical({"schema": "static-provenance-test-v1", "status": "accepted"})
        ciphertext = b"FAKE-AGE\x00" + self.policy.webapp_fi_age_recipient.encode("ascii") + b"\x00" + plaintext
        descriptor = {
            "object_key": contract.source_object_key(self.policy, request),
            "version_id": self.runner.get_version,
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "ciphertext_bytes": len(ciphertext),
            "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            "plaintext_bytes": len(plaintext),
        }
        controller_receipt = contract.build_publish_receipt(config=self.policy, request=request, descriptor=descriptor)
        receipt_path = self.root / "controller-publish-receipt.json"
        receipt_path.write_bytes(canonical(controller_receipt))
        receipt_path.chmod(0o600)
        self.runner.download_payload = ciphertext
        destination = self.workspace / "received-provenance"
        result = exchange.receive_static_provenance(
            policy=self.policy,
            controller_publish_receipt_path=receipt_path,
            download_url=self._presigned_url(key=descriptor["object_key"], version_id=self.runner.get_version),
            age_identity_file=self.identity,
            destination_dir=destination,
            command_runner=self.runner,
        )
        self.assertEqual("received", result["status"])
        self.assertEqual(plaintext, (destination / exchange.RECEIVED_PROVENANCE_NAME).read_bytes())
        self.assertTrue((destination / exchange.RECEIVE_RECEIPT_NAME).is_file())
        self.assertFalse((destination / "payload.age").exists())
        self.assertFalse((destination / "response.headers").exists())
        self.assertNotIn(b"://", (destination / exchange.RECEIVE_RECEIPT_NAME).read_bytes())

    def test_receive_rejects_response_version_mismatch_without_destination(self) -> None:
        request = self._request(
            source_site="controller",
            destination_site="webapp_fi",
            object_kind=contract.STATIC_PROVENANCE_OBJECT_KIND,
            object_id="provenance-20260730-02",
            mode=contract.SINGLE_MODE,
            recipients=(self.policy.webapp_fi_age_recipient,),
        )
        plaintext = canonical({"schema": "static-provenance-test-v1", "status": "accepted"})
        ciphertext = b"FAKE-AGE\x00" + self.policy.webapp_fi_age_recipient.encode("ascii") + b"\x00" + plaintext
        descriptor = {
            "object_key": contract.source_object_key(self.policy, request),
            "version_id": "expected-version-02",
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "ciphertext_bytes": len(ciphertext),
            "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            "plaintext_bytes": len(plaintext),
        }
        controller_receipt = contract.build_publish_receipt(config=self.policy, request=request, descriptor=descriptor)
        receipt_path = self.root / "controller-publish-receipt.json"
        receipt_path.write_bytes(canonical(controller_receipt))
        receipt_path.chmod(0o600)
        self.runner.download_payload = ciphertext
        destination = self.workspace / "wrong-version"
        with self.assertRaisesRegex(exchange.SourceExchangeError, "different VersionId"):
            exchange.receive_static_provenance(
                policy=self.policy,
                controller_publish_receipt_path=receipt_path,
                download_url=self._presigned_url(key=descriptor["object_key"], version_id=descriptor["version_id"]),
                age_identity_file=self.identity,
                destination_dir=destination,
                command_runner=self.runner,
            )
        self.assertFalse(destination.exists())

    def test_policy_rejects_controller_credentials_and_helper_has_no_controller_transport_import(self) -> None:
        self._write_policy(credentials_file="/root/never-copy-this")
        with self.assertRaisesRegex(exchange.SourceExchangeError, "policy is unsupported"):
            exchange.load_policy(self.policy_path)

        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        forbidden = {
            "boto3",
            "botocore",
            "docker",
            "requests",
            "manage_webapp_fi_source_transport",
            "manage_webapp_ir_snapshot",
            "verify_webapp_fi_source_provenance",
        }
        self.assertFalse(imports & forbidden)
        self.assertIn("subprocess", imports)

    def test_url_free_controller_packet_policy_reconstructs_endpoint_only_in_memory(self) -> None:
        policy = {
            "schema": exchange.STATIC_PROVENANCE_POLICY_SCHEMA,
            "endpoint_host": "s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "private-artifacts",
            "prefix": "campaigns/three-site",
            "age_binary": "/usr/bin/age",
            "workspace": str(self.workspace),
            "controller_age_recipient": recipient("a"),
            "webapp_fi_age_recipient": recipient("c"),
            "webapp_ir_age_recipient": recipient("d"),
            "maximum_plaintext_bytes": 1024 * 1024,
        }
        payload = canonical(policy)
        self.assertNotIn(b"://", payload)
        self.policy_path.write_bytes(payload)
        self.policy_path.chmod(0o600)
        loaded = exchange.load_policy(self.policy_path)
        self.assertEqual("https://s3.ir-thr-at1.arvanstorage.ir", loaded.endpoint)
        self.assertEqual(self.workspace, loaded.workspace)


class WebAppFiSourceExchangeLoaderTests(unittest.TestCase):
    def _fixture_code_file(self, root: Path, name: str, payload: str = "# fixture\n") -> Path:
        path = root / name
        path.write_text(payload, encoding="utf-8")
        path.chmod(0o644)
        return path

    def test_exact_sibling_loader_rejects_writable_sibling_and_unsafe_leaf(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fi-source-exchange-loader-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            source = self._fixture_code_file(root, "exchange.py")
            sibling = self._fixture_code_file(root, "writable-sibling.py")
            sibling.chmod(0o666)
            module_name = "_fi_source_exchange_writable_sibling_fixture"
            with mock.patch.object(exchange, "__file__", str(source)):
                with self.assertRaisesRegex(RuntimeError, "root-owned non-writable regular non-symlink"):
                    exchange._load_exact_sibling(sibling.name, module_name)
                with self.assertRaisesRegex(RuntimeError, "safe leaf name"):
                    exchange._load_exact_sibling("../writable-sibling.py", module_name)
            self.assertNotIn(module_name, sys.modules)

    def test_exact_sibling_loader_rejects_symlink_and_writable_ancestor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fi-source-exchange-loader-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            source = self._fixture_code_file(root, "exchange.py")
            target = self._fixture_code_file(root, "target.py", "raise AssertionError('must not execute')\n")
            sibling = root / "linked-sibling.py"
            sibling.symlink_to(target.name)
            linked_name = "_fi_source_exchange_symlink_sibling_fixture"
            with mock.patch.object(exchange, "__file__", str(source)):
                with self.assertRaisesRegex(RuntimeError, "root-owned non-writable regular non-symlink"):
                    exchange._load_exact_sibling(sibling.name, linked_name)
            self.assertNotIn(linked_name, sys.modules)

            unsafe_parent = root / "unsafe-parent"
            unsafe_parent.mkdir(mode=0o777)
            unsafe_parent.chmod(0o777)
            unsafe_source = self._fixture_code_file(unsafe_parent, "exchange.py")
            unsafe_sibling = self._fixture_code_file(unsafe_parent, "sibling.py")
            ancestor_name = "_fi_source_exchange_writable_ancestor_fixture"
            with mock.patch.object(exchange, "__file__", str(unsafe_source)):
                with self.assertRaisesRegex(RuntimeError, "parent is not root-controlled"):
                    exchange._load_exact_sibling(unsafe_sibling.name, ancestor_name)
            self.assertNotIn(ancestor_name, sys.modules)

    def test_exact_sibling_loader_restores_prior_module_after_import_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fi-source-exchange-loader-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            source = self._fixture_code_file(root, "exchange.py")
            sibling = self._fixture_code_file(root, "broken.py", "raise RuntimeError('fixture import failure')\n")
            module_name = "_fi_source_exchange_failed_import_fixture"
            previous = object()
            original = sys.modules.get(module_name)
            sys.modules[module_name] = previous
            try:
                with mock.patch.object(exchange, "__file__", str(source)):
                    with self.assertRaisesRegex(RuntimeError, "fixture import failure"):
                        exchange._load_exact_sibling(sibling.name, module_name)
                self.assertIs(previous, sys.modules.get(module_name))
            finally:
                if original is None:
                    sys.modules.pop(module_name, None)
                else:
                    sys.modules[module_name] = original


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
