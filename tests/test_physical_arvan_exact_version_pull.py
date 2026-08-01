from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from core.physical_arvan_exact_version_pull import (
    ArvanExactVersionPullError,
    ArvanExactVersionPullExpectation,
    ArvanExactVersionPullReader,
    RootOwnedArvanExactVersionPullConfig,
    validate_arvan_exact_version_pull_config,
)
from core.physical_wal_receiver_staging import PhysicalWalExactVersionReadback


OBJECT_KEY = "physical-recovery/v2/campaign-20260731/base/9f86d081884c7d659a2feaa0c55ad015.age"
VERSION_ID = "version-001_A+="


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _metadata(payload: bytes) -> dict[str, str]:
    return {
        "transport-schema": "gold-trade-physical-wal-object-storage-uploader-v1",
        "encryption": "age-v1",
        "descriptor-sha256": "a" * 64,
        "destination-age-recipient": "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
        "ciphertext-sha256": _digest(payload),
        "ciphertext-bytes": str(len(payload)),
    }


class _Body:
    def __init__(
        self,
        payload: bytes,
        *,
        chunk_bytes: int = 3,
        fail_on_read: bool = False,
        non_bytes: object | None = None,
        fail_on_close: bool = False,
    ) -> None:
        self._payload = payload
        self._offset = 0
        self._chunk_bytes = chunk_bytes
        self._fail_on_read = fail_on_read
        self._non_bytes = non_bytes
        self._fail_on_close = fail_on_close
        self.closed = False

    def read(self, _maximum: int) -> bytes:
        if self._fail_on_read:
            raise RuntimeError("secret=should-never-appear-in-error")
        if self._non_bytes is not None:
            return self._non_bytes  # type: ignore[return-value]
        if self._offset >= len(self._payload):
            return b""
        result = self._payload[self._offset : self._offset + self._chunk_bytes]
        self._offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True
        if self._fail_on_close:
            raise RuntimeError("credential=should-never-appear-in-error")


class _Client:
    def __init__(self, response: dict[str, object] | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _Factory:
    def __init__(self, client: object, *, failure: Exception | None = None) -> None:
        self.client = client
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if self.failure is not None:
            raise self.failure
        return self.client


class PhysicalArvanExactVersionPullTests(unittest.TestCase):
    def config(
        self,
        *,
        endpoint: str = "https://s3.ir-thr-at1.arvanstorage.ir/",
        region: str = "ir-thr-at1",
        enabled: bool = True,
        maximum: int = 1024 * 1024,
        **changes: object,
    ) -> RootOwnedArvanExactVersionPullConfig:
        values: dict[str, object] = {
            "endpoint": endpoint,
            "region": region,
            "bucket": "private-physical-recovery",
            "maximum_ciphertext_bytes": maximum,
            "enabled": enabled,
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
        }
        values.update(changes)
        return RootOwnedArvanExactVersionPullConfig(**values)  # type: ignore[arg-type]

    def expectation(
        self,
        payload: bytes,
        *,
        object_key: str = OBJECT_KEY,
        version_id: str = VERSION_ID,
        metadata: dict[str, str] | None = None,
        ciphertext_sha256: str | None = None,
        ciphertext_bytes: int | None = None,
    ) -> ArvanExactVersionPullExpectation:
        return ArvanExactVersionPullExpectation(
            object_key=object_key,
            version_id=version_id,
            ciphertext_sha256=_digest(payload) if ciphertext_sha256 is None else ciphertext_sha256,
            ciphertext_bytes=len(payload) if ciphertext_bytes is None else ciphertext_bytes,
            metadata=_metadata(payload) if metadata is None else metadata,
        )

    def response(
        self,
        payload: bytes,
        *,
        body: _Body | None = None,
        metadata: dict[str, str] | None = None,
        version_id: object = VERSION_ID,
        content_length: object | None = None,
        **changes: object,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "VersionId": version_id,
            "Key": OBJECT_KEY,
            "ContentLength": len(payload) if content_length is None else content_length,
            "Metadata": _metadata(payload) if metadata is None else metadata,
            "Body": _Body(payload) if body is None else body,
            "ResponseMetadata": {"HTTPStatusCode": 200, "HTTPHeaders": {"etag": "fixture"}},
        }
        result.update(changes)
        return result

    def reader(
        self,
        payload: bytes,
        *,
        response: dict[str, object] | Exception | None = None,
        config: RootOwnedArvanExactVersionPullConfig | None = None,
        expectation: ArvanExactVersionPullExpectation | None = None,
        factory: _Factory | None = None,
    ) -> tuple[ArvanExactVersionPullReader, _Client, _Factory]:
        client = _Client(self.response(payload) if response is None else response)
        supplied_factory = _Factory(client) if factory is None else factory
        return (
            ArvanExactVersionPullReader(
                config=self.config() if config is None else config,
                client_factory=supplied_factory,
                expectations=(self.expectation(payload) if expectation is None else expectation,),
            ),
            client,
            supplied_factory,
        )

    def read_to_new_file(
        self, reader: ArvanExactVersionPullReader, *, key: str = OBJECT_KEY, version: str = VERSION_ID
    ) -> tuple[PhysicalWalExactVersionReadback, bytes]:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ciphertext.age"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                result = reader.read_exact_to_fd(
                    object_key=key,
                    version_id=version,
                    destination_fd=descriptor,
                )
            finally:
                os.close(descriptor)
            return result, path.read_bytes()

    def test_streams_exact_pinned_version_and_returns_wal_reader_receipt(self) -> None:
        payload = b"age-encryption.org/v1\nfixture-ciphertext" * 8
        reader, client, factory = self.reader(payload)

        result, stored = self.read_to_new_file(reader)

        self.assertIs(type(result), PhysicalWalExactVersionReadback)
        self.assertEqual(OBJECT_KEY, result.object_key)
        self.assertEqual(VERSION_ID, result.version_id)
        self.assertEqual(_digest(payload), result.ciphertext_sha256)
        self.assertEqual(len(payload), result.ciphertext_bytes)
        self.assertEqual(payload, stored)
        self.assertEqual(
            [{"endpoint": "https://s3.ir-thr-at1.arvanstorage.ir", "region": "ir-thr-at1"}],
            factory.calls,
        )
        self.assertEqual(
            [{"Bucket": "private-physical-recovery", "Key": OBJECT_KEY, "VersionId": VERSION_ID}],
            client.calls,
        )
        self.assertFalse(hasattr(client, "list_object_versions"))

    def test_accepts_the_long_witnessed_term_component_used_by_blob_v2_keys(self) -> None:
        payload = b"blob-ciphertext"
        blob_key = "/".join(
            (
                "physical-blobs-v2",
                "campaign-20260731",
                "3138d0c2a8d20a84042c3a438fbc88db7a4db498",
                "baseline-20260731",
                "webapp_fi-to-webapp_ir",
                "timeline-00000007",
                "route-" + "b" * 64,
                "term-00000000000000000041-" + "c" * 64 + "-" + "d" * 64,
                "blobs",
                "blob-" + "e" * 64,
                "f" * 64 + ".age",
            )
        )
        expectation = self.expectation(payload, object_key=blob_key)
        reader, _client, _factory = self.reader(
            payload,
            expectation=expectation,
            response=self.response(payload, Key=blob_key),
        )

        result, stored = self.read_to_new_file(reader, key=blob_key)

        self.assertEqual(blob_key, result.object_key)
        self.assertEqual(payload, stored)

    def test_config_rejects_noncanonical_endpoint_region_bucket_and_bool_size_before_factory(self) -> None:
        valid_payload = b"ciphertext"
        factory = _Factory(_Client(self.response(valid_payload)))
        invalid_cases = (
            self.config(schema="unexpected-schema"),
            self.config(endpoint="http://s3.ir-thr-at1.arvanstorage.ir"),
            self.config(endpoint="https://s3.ir-thr-at1.arvanstorage.ir.evil.example"),
            self.config(endpoint="https://s3.ir-thr-at1.arvanstorage.ir/other"),
            self.config(region="us-east-1"),
            self.config(bucket="private/recovery"),
            self.config(maximum=True),
        )
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ArvanExactVersionPullError):
                    ArvanExactVersionPullReader(
                        config=invalid,
                        client_factory=factory,
                        expectations=(self.expectation(valid_payload),),
                    )
        self.assertEqual([], factory.calls)

    def test_disabled_reader_never_creates_client(self) -> None:
        payload = b"ciphertext"
        reader, _client, factory = self.reader(payload, config=self.config(enabled=False))
        with tempfile.TemporaryDirectory() as temporary:
            descriptor = os.open(Path(temporary) / "ciphertext.age", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with self.assertRaisesRegex(ArvanExactVersionPullError, "ARVAN_PULL_DISABLED"):
                    reader.read_exact_to_fd(
                        object_key=OBJECT_KEY,
                        version_id=VERSION_ID,
                        destination_fd=descriptor,
                    )
            finally:
                os.close(descriptor)
        self.assertEqual([], factory.calls)

    def test_rejects_mutable_aliases_and_path_traversal_before_factory(self) -> None:
        payload = b"ciphertext"
        reader, _client, factory = self.reader(payload)
        selectors = (
            ("physical-recovery/v2/latest/object.age", VERSION_ID),
            ("physical-recovery/v2/../object.age", VERSION_ID),
            (OBJECT_KEY, "latest"),
            (OBJECT_KEY, "null"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for ordinal, (key, version) in enumerate(selectors):
                descriptor = os.open(
                    Path(temporary) / f"ciphertext-{ordinal}.age",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                try:
                    with self.subTest(key=key, version=version):
                        with self.assertRaisesRegex(
                            ArvanExactVersionPullError, "ARVAN_PULL_OBJECT_SELECTOR_INVALID"
                        ):
                            reader.read_exact_to_fd(
                                object_key=key,
                                version_id=version,
                                destination_fd=descriptor,
                            )
                finally:
                    os.close(descriptor)
        self.assertEqual([], factory.calls)

    def test_expectation_is_fixed_and_rejects_bool_int_duplicate_and_secret_metadata(self) -> None:
        payload = b"ciphertext"
        valid = self.expectation(payload)
        cases = (
            (valid, ArvanExactVersionPullExpectation(
                object_key=OBJECT_KEY,
                version_id=VERSION_ID,
                ciphertext_sha256=_digest(payload),
                ciphertext_bytes=True,  # type: ignore[arg-type]
                metadata=_metadata(payload),
            )),
            (valid, valid),
            (ArvanExactVersionPullExpectation(
                object_key="physical-recovery/v2/latest/object.age",
                version_id=VERSION_ID,
                ciphertext_sha256=_digest(payload),
                ciphertext_bytes=len(payload),
                metadata=_metadata(payload),
            ),),
            (ArvanExactVersionPullExpectation(
                object_key=OBJECT_KEY,
                version_id=VERSION_ID,
                ciphertext_sha256=_digest(payload),
                ciphertext_bytes=len(payload),
                metadata={**_metadata(payload), "access-key": "not-allowed"},
            ),),
            (ArvanExactVersionPullExpectation(
                object_key=OBJECT_KEY,
                version_id=VERSION_ID,
                ciphertext_sha256=_digest(payload),
                ciphertext_bytes=len(payload),
                metadata={**_metadata(payload), "x-amz-server-side-encryption": "AES256"},
            ),),
        )
        factory = _Factory(_Client(self.response(payload)))
        for values in cases:
            expectations = values if len(values) != 2 else values
            with self.subTest(expectations=expectations):
                with self.assertRaises(ArvanExactVersionPullError):
                    ArvanExactVersionPullReader(
                        config=self.config(),
                        client_factory=factory,
                        expectations=expectations,
                    )
        self.assertEqual([], factory.calls)

    def test_rejects_unpinned_selector_before_client_factory(self) -> None:
        payload = b"ciphertext"
        reader, _client, factory = self.reader(payload)
        with tempfile.TemporaryDirectory() as temporary:
            descriptor = os.open(Path(temporary) / "ciphertext.age", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with self.assertRaisesRegex(ArvanExactVersionPullError, "ARVAN_PULL_OBJECT_NOT_PINNED"):
                    reader.read_exact_to_fd(
                        object_key="physical-recovery/v2/campaign-20260731/base/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.age",
                        version_id=VERSION_ID,
                        destination_fd=descriptor,
                    )
            finally:
                os.close(descriptor)
        self.assertEqual([], factory.calls)

    def test_rejects_version_content_length_key_and_metadata_mismatches(self) -> None:
        payload = b"trusted-ciphertext"
        mismatches = (
            self.response(payload, version_id="foreign-version"),
            self.response(payload, content_length=True),
            self.response(payload, Key="physical-recovery/v2/campaign-20260731/base/foreign.age"),
            self.response(payload, metadata={**_metadata(payload), "unexpected": "field"}),
        )
        for response in mismatches:
            with self.subTest(response=response):
                reader, _client, _factory = self.reader(payload, response=response)
                with self.assertRaises(ArvanExactVersionPullError):
                    self.read_to_new_file(reader)

    def test_rejects_redirect_and_provider_encryption_fields_even_when_nested(self) -> None:
        payload = b"trusted-ciphertext"
        unsafe = (
            self.response(payload, WebsiteRedirectLocation="https://evil.invalid/object"),
            self.response(payload, ServerSideEncryption="AES256"),
            self.response(
                payload,
                ResponseMetadata={
                    "HTTPStatusCode": 200,
                    "HTTPHeaders": {"x-amz-server-side-encryption": "AES256"},
                },
            ),
            self.response(
                payload,
                ResponseMetadata={"HTTPStatusCode": 302, "HTTPHeaders": {"location": "https://evil.invalid"}},
            ),
        )
        for response in unsafe:
            with self.subTest(response=response):
                reader, _client, _factory = self.reader(payload, response=response)
                with self.assertRaisesRegex(ArvanExactVersionPullError, "ARVAN_GET_RESPONSE_UNSAFE"):
                    self.read_to_new_file(reader)

    def test_streaming_hash_size_body_and_close_failures_fail_closed_without_leaking_client_error(self) -> None:
        payload = b"trusted-ciphertext"
        short_body = _Body(payload[:-1])
        altered_body = _Body(b"x" * len(payload))
        read_failure = _Body(payload, fail_on_read=True)
        non_bytes = _Body(payload, non_bytes="not-bytes")
        close_failure = _Body(payload, fail_on_close=True)
        cases = (
            (self.response(payload, body=short_body), "ARVAN_GET_SIZE_MISMATCH", short_body),
            (self.response(payload, body=altered_body), "ARVAN_GET_HASH_MISMATCH", altered_body),
            (self.response(payload, body=read_failure), "ARVAN_GET_BODY_FAILED", read_failure),
            (self.response(payload, body=non_bytes), "ARVAN_GET_BODY_INVALID", non_bytes),
            (self.response(payload, body=close_failure), "ARVAN_GET_BODY_CLOSE_FAILED", close_failure),
        )
        for response, code, body in cases:
            with self.subTest(code=code):
                reader, _client, _factory = self.reader(payload, response=response)
                with self.assertRaisesRegex(ArvanExactVersionPullError, code) as raised:
                    self.read_to_new_file(reader)
                self.assertNotIn("secret", str(raised.exception).lower())
                self.assertNotIn("credential", str(raised.exception).lower())
                self.assertTrue(body.closed)

    def test_client_factory_and_get_exceptions_are_redacted(self) -> None:
        payload = b"ciphertext"
        client = _Client(RuntimeError("secret=factory-client-error"))
        reader, _unused, _factory = self.reader(payload, factory=_Factory(client))
        with self.assertRaisesRegex(ArvanExactVersionPullError, "ARVAN_GET_FAILED") as raised:
            self.read_to_new_file(reader)
        self.assertNotIn("secret", str(raised.exception).lower())
        self.assertIsNone(raised.exception.__cause__)

        factory_failure = _Factory(client, failure=RuntimeError("token=hidden"))
        reader, _unused, _factory = self.reader(payload, factory=factory_failure)
        with self.assertRaisesRegex(ArvanExactVersionPullError, "ARVAN_CLIENT_FACTORY_FAILED") as raised:
            self.read_to_new_file(reader)
        self.assertNotIn("token", str(raised.exception).lower())
        self.assertIsNone(raised.exception.__cause__)

    def test_rejects_invalid_destination_fd_before_factory(self) -> None:
        payload = b"ciphertext"
        reader, _client, factory = self.reader(payload)
        with self.assertRaisesRegex(ArvanExactVersionPullError, "ARVAN_PULL_DESTINATION_FD_INVALID"):
            reader.read_exact_to_fd(
                object_key=OBJECT_KEY,
                version_id=VERSION_ID,
                destination_fd=True,  # type: ignore[arg-type]
            )
        self.assertEqual([], factory.calls)

    def test_config_validation_is_pure_and_normalizes_only_trailing_slash(self) -> None:
        validated = validate_arvan_exact_version_pull_config(self.config())
        self.assertEqual("https://s3.ir-thr-at1.arvanstorage.ir", validated.endpoint)
        self.assertEqual("ir-thr-at1", validated.region)
        self.assertTrue(validated.enabled)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
