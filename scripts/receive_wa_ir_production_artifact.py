#!/usr/bin/env python3
"""Receive one production WA-IR artifact without exposing its presigned URL.

The bounded JSON descriptor is accepted only on stdin.  This keeps the URL out
of argv and the environment.  The receiver downloads and decrypts one exact
object version, installs one create-only file, emits non-secret JSON, and does
not extract archives, load images, or invoke Compose.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import subprocess
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import (
    HTTPSHandler,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.wa_ir_production_transport_contract import (
    AGE_EXECUTABLE,
    ARVAN_HOST,
    ARVAN_REGION,
    MAX_PAYLOAD_BYTES,
    MAX_READBACK_BYTES,
    PRODUCTION_BUCKET,
    ProductionTransportError,
    WA_IR_AGE_IDENTITY_FILE,
    validate_object_key_binding,
)


DESCRIPTOR_SCHEMA = "wa-ir-production-artifact-receive-v1"
ATTESTATION_SCHEMA = "wa-ir-production-artifact-attestation-v1"
OPERATIONS_ROOT = Path("/srv/trading-bot/dark-standby/operations")
DEPLOY_ROOT = Path("/srv/trading-bot")
AGE_IDENTITY_FILE = WA_IR_AGE_IDENTITY_FILE
MAX_DESCRIPTOR_BYTES = 32 * 1024
MAX_IDENTITY_BYTES = 16 * 1024
MAX_URL_BYTES = 16 * 1024
MIN_URL_TTL_SECONDS = 60
MAX_URL_TTL_SECONDS = 900
CLOCK_SKEW_SECONDS = 300
DOWNLOAD_TIMEOUT_SECONDS = 120
DECRYPT_TIMEOUT_SECONDS = 3600
SYSTEM_CA_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ARTIFACT_KIND_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_DESTINATION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_VERSION_RE = re.compile(r"^[\x21-\x7e]{1,1024}$")
_REQUIRED_QUERY_FIELDS = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-Signature",
        "X-Amz-SignedHeaders",
        "versionId",
    }
)
_OPTIONAL_QUERY_FIELDS = frozenset({"X-Amz-Security-Token"})
_DESCRIPTOR_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "artifact_kind",
        "destination_name",
        "bucket",
        "object_key",
        "version_id",
        "url",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "plaintext_sha256",
        "plaintext_bytes",
    }
)
_SAFE_AGE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


class ProductionReceiveError(RuntimeError):
    """A redacted, operator-safe receiver failure."""


@dataclass(frozen=True)
class ReceiveDescriptor:
    operation_id: str
    artifact_kind: str
    destination_name: str
    bucket: str
    object_key: str
    version_id: str
    url: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_sha256: str
    plaintext_bytes: int


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, ARG002
        return None


def _bounded_positive_integer(value: Any, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductionReceiveError("artifact descriptor has an invalid size")
    if not 1 <= value <= maximum:
        raise ProductionReceiveError("artifact descriptor size is outside its bound")
    return value


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_presigned_url(
    url: str,
    *,
    bucket: str,
    object_key: str,
    version_id: str,
    now: datetime | None = None,
) -> None:
    if not isinstance(url, str) or not 1 <= len(url.encode("utf-8")) <= MAX_URL_BYTES:
        raise ProductionReceiveError("presigned URL is invalid")
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        port = parsed.port
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ProductionReceiveError("presigned URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != ARVAN_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or parsed.path != f"/{bucket}/{object_key}"
        or set(query) - _REQUIRED_QUERY_FIELDS - _OPTIONAL_QUERY_FIELDS
        or _REQUIRED_QUERY_FIELDS - set(query)
        or any(len(values) != 1 or not values[0] for values in query.values())
        or query.get("versionId") != [version_id]
        or query.get("X-Amz-Algorithm") != ["AWS4-HMAC-SHA256"]
        or not re.fullmatch(r"[0-9a-fA-F]{64}", query["X-Amz-Signature"][0])
        or "host" not in query["X-Amz-SignedHeaders"][0].split(";")
        or len(query["X-Amz-Credential"][0]) > 512
    ):
        raise ProductionReceiveError(
            "presigned URL is outside the exact Arvan object-version scope"
        )
    try:
        ttl = int(query["X-Amz-Expires"][0])
        issued_at = datetime.strptime(
            query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ"
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ProductionReceiveError("presigned URL time binding is invalid") from exc
    credential = query["X-Amz-Credential"][0].split("/")
    if (
        len(credential) != 5
        or not credential[0]
        or credential[1] != issued_at.strftime("%Y%m%d")
        or credential[2] != ARVAN_REGION
        or credential[3:] != ["s3", "aws4_request"]
    ):
        raise ProductionReceiveError("presigned URL credential scope is invalid")
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ProductionReceiveError("receiver clock is not timezone-aware")
    observed_at = observed_at.astimezone(timezone.utc)
    if (
        not MIN_URL_TTL_SECONDS <= ttl <= MAX_URL_TTL_SECONDS
        or observed_at < issued_at - timedelta(seconds=CLOCK_SKEW_SECONDS)
        or observed_at > issued_at + timedelta(seconds=ttl)
    ):
        raise ProductionReceiveError("presigned URL is expired or outside its time bound")


def parse_descriptor(
    payload: bytes,
    *,
    now: datetime | None = None,
) -> ReceiveDescriptor:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_DESCRIPTOR_BYTES:
        raise ProductionReceiveError("artifact descriptor is empty or oversized")
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProductionReceiveError("artifact descriptor is not valid JSON") from exc
    if (
        not isinstance(document, dict)
        or set(document) != _DESCRIPTOR_FIELDS
        or document.get("schema") != DESCRIPTOR_SCHEMA
    ):
        raise ProductionReceiveError("artifact descriptor schema or fields are invalid")

    operation_id = document.get("operation_id")
    artifact_kind = document.get("artifact_kind")
    destination_name = document.get("destination_name")
    bucket = document.get("bucket")
    object_key = document.get("object_key")
    version_id = document.get("version_id")
    url = document.get("url")
    ciphertext_sha256 = document.get("ciphertext_sha256")
    plaintext_sha256 = document.get("plaintext_sha256")
    if (
        not isinstance(operation_id, str)
        or not _UUID_RE.fullmatch(operation_id)
        or not isinstance(artifact_kind, str)
        or not _ARTIFACT_KIND_RE.fullmatch(artifact_kind)
        or not isinstance(destination_name, str)
        or not _DESTINATION_RE.fullmatch(destination_name)
        or destination_name in {".", ".."}
        or bucket != PRODUCTION_BUCKET
        or not isinstance(object_key, str)
        or not isinstance(version_id, str)
        or not _VERSION_RE.fullmatch(version_id)
        or any(character.isspace() for character in version_id)
        or not isinstance(ciphertext_sha256, str)
        or not _SHA256_RE.fullmatch(ciphertext_sha256)
        or not isinstance(plaintext_sha256, str)
        or not _SHA256_RE.fullmatch(plaintext_sha256)
    ):
        raise ProductionReceiveError("artifact descriptor identity is invalid")
    ciphertext_bytes = _bounded_positive_integer(
        document.get("ciphertext_bytes"),
        maximum=MAX_READBACK_BYTES,
    )
    plaintext_bytes = _bounded_positive_integer(
        document.get("plaintext_bytes"),
        maximum=MAX_PAYLOAD_BYTES,
    )
    try:
        validate_object_key_binding(
            object_key,
            operation_id=operation_id,
            artifact_kind=artifact_kind,
            ciphertext_sha256=ciphertext_sha256,
        )
    except ProductionTransportError as exc:
        raise ProductionReceiveError("artifact object key binding is invalid") from exc
    _validate_presigned_url(
        url,
        bucket=bucket,
        object_key=object_key,
        version_id=version_id,
        now=now,
    )
    return ReceiveDescriptor(
        operation_id=operation_id,
        artifact_kind=artifact_kind,
        destination_name=destination_name,
        bucket=bucket,
        object_key=object_key,
        version_id=version_id,
        url=url,
        ciphertext_sha256=ciphertext_sha256,
        ciphertext_bytes=ciphertext_bytes,
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=plaintext_bytes,
    )


def _header(headers: Any, name: str) -> str:
    value = headers.get(name) if callable(getattr(headers, "get", None)) else None
    if value is None and callable(getattr(headers, "get", None)):
        value = headers.get(name.lower())
    return str(value or "")


def _open_url(request: Request):  # noqa: ANN201
    try:
        ca_metadata = SYSTEM_CA_BUNDLE.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionReceiveError("system TLS trust store is unavailable") from exc
    if (
        not stat.S_ISREG(ca_metadata.st_mode)
        or ca_metadata.st_uid != 0
        or stat.S_IMODE(ca_metadata.st_mode) & 0o022
    ):
        raise ProductionReceiveError("system TLS trust store is unsafe")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_verify_locations(cafile=str(SYSTEM_CA_BUNDLE))
    except (OSError, ssl.SSLError) as exc:
        raise ProductionReceiveError("system TLS trust store cannot be loaded") from exc
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=context),
        _NoRedirect(),
    )
    return opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise ProductionReceiveError("artifact file write made no progress")
        written += count


def _download_ciphertext(descriptor: ReceiveDescriptor, output: Path) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    output_fd = -1
    try:
        output_fd = os.open(output, flags, 0o600)
        request = Request(
            descriptor.url,
            method="GET",
            headers={
                "Accept": "application/octet-stream",
                "Accept-Encoding": "identity",
                "User-Agent": "wa-ir-production-receiver/1",
            },
        )
        try:
            response = _open_url(request)
        except (HTTPError, URLError, OSError, ValueError):
            raise ProductionReceiveError(
                "exact-version artifact download failed"
            ) from None
        digest = hashlib.sha256()
        observed_bytes = 0
        try:
            status = getattr(response, "status", None)
            if status is None and callable(getattr(response, "getcode", None)):
                status = response.getcode()
            final_url = (
                response.geturl()
                if callable(getattr(response, "geturl", None))
                else ""
            )
            content_length = _header(response.headers, "Content-Length")
            content_encoding = _header(response.headers, "Content-Encoding")
            response_version = _header(response.headers, "x-amz-version-id")
            if (
                status != 200
                or final_url != descriptor.url
                or content_length != str(descriptor.ciphertext_bytes)
                or content_encoding not in {"", "identity"}
                or response_version != descriptor.version_id
            ):
                raise ProductionReceiveError(
                    "exact-version artifact response binding differs"
                )
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise ProductionReceiveError(
                        "artifact download returned invalid bytes"
                    )
                observed_bytes += len(chunk)
                if observed_bytes > descriptor.ciphertext_bytes:
                    raise ProductionReceiveError(
                        "artifact ciphertext exceeded its declared size"
                    )
                digest.update(chunk)
                _write_all(output_fd, chunk)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if (
            observed_bytes != descriptor.ciphertext_bytes
            or digest.hexdigest() != descriptor.ciphertext_sha256
        ):
            raise ProductionReceiveError(
                "artifact ciphertext hash or size differs"
            )
        os.fchmod(output_fd, 0o600)
        os.fsync(output_fd)
        output_metadata = os.fstat(output_fd)
        if (
            not stat.S_ISREG(output_metadata.st_mode)
            or output_metadata.st_uid != os.geteuid()
            or output_metadata.st_nlink != 1
            or stat.S_IMODE(output_metadata.st_mode) != 0o600
            or output_metadata.st_size != descriptor.ciphertext_bytes
        ):
            raise ProductionReceiveError(
                "artifact ciphertext staging identity differs"
            )
    except ProductionReceiveError:
        if output_fd >= 0:
            os.close(output_fd)
            output_fd = -1
        output.unlink(missing_ok=True)
        raise
    except Exception:
        if output_fd >= 0:
            os.close(output_fd)
            output_fd = -1
        output.unlink(missing_ok=True)
        raise ProductionReceiveError("artifact download failed closed") from None
    finally:
        if output_fd >= 0:
            os.close(output_fd)


def _open_secure_identity(path: Path, *, required_uid: int) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionReceiveError(
            "age identity is unavailable or unsafe"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != required_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
            or not 1 <= metadata.st_size <= MAX_IDENTITY_BYTES
        ):
            raise ProductionReceiveError(
                "age identity permissions or ownership are unsafe"
            )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_secure_ciphertext(path: Path, *, required_uid: int) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionReceiveError("ciphertext staging file is unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != required_uid
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size <= 0
            or metadata.st_size > MAX_READBACK_BYTES
        ):
            raise ProductionReceiveError("ciphertext staging file is unsafe")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _decrypt_age(
    ciphertext: Path,
    *,
    plaintext_directory_fd: int,
    plaintext_name: str,
    identity_file: Path,
    required_uid: int,
) -> None:
    try:
        os.stat(
            plaintext_name,
            dir_fd=plaintext_directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ProductionReceiveError("plaintext staging path is unsafe") from exc
    else:
        raise ProductionReceiveError("plaintext staging path already exists")
    try:
        executable = AGE_EXECUTABLE.stat(follow_symlinks=False)
    except OSError as exc:
        raise ProductionReceiveError("age executable is unavailable") from exc
    if (
        not stat.S_ISREG(executable.st_mode)
        or executable.st_uid != 0
        or stat.S_IMODE(executable.st_mode) & 0o022
    ):
        raise ProductionReceiveError("age executable is unsafe")

    identity_fd = ciphertext_fd = -1
    try:
        identity_fd = _open_secure_identity(identity_file, required_uid=required_uid)
        ciphertext_fd = _open_secure_ciphertext(ciphertext, required_uid=required_uid)
        plaintext_path = (
            f"/proc/self/fd/{plaintext_directory_fd}/{plaintext_name}"
        )
        try:
            result = subprocess.run(
                [
                    str(AGE_EXECUTABLE),
                    "--decrypt",
                    "--identity",
                    f"/proc/self/fd/{identity_fd}",
                    "--output",
                    plaintext_path,
                ],
                check=False,
                stdin=ciphertext_fd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                pass_fds=(identity_fd, plaintext_directory_fd),
                timeout=DECRYPT_TIMEOUT_SECONDS,
                env=_SAFE_AGE_ENV,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            try:
                os.unlink(plaintext_name, dir_fd=plaintext_directory_fd)
            except FileNotFoundError:
                pass
            raise ProductionReceiveError("age decryption failed closed") from exc
        if result.returncode != 0:
            try:
                os.unlink(plaintext_name, dir_fd=plaintext_directory_fd)
            except FileNotFoundError:
                pass
            raise ProductionReceiveError("age decryption failed closed")
    finally:
        if ciphertext_fd >= 0:
            os.close(ciphertext_fd)
        if identity_fd >= 0:
            os.close(identity_fd)


def _open_secure_directory(
    path: Path,
    *,
    required_uid: int,
    exact_mode: int | None,
) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionReceiveError(
            "receiver directory is unavailable or unsafe"
        ) from exc
    metadata = os.fstat(descriptor)
    observed_mode = stat.S_IMODE(metadata.st_mode)
    mode_is_unsafe = (
        observed_mode != exact_mode
        if exact_mode is not None
        else bool(observed_mode & 0o022)
    )
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != required_uid
        or mode_is_unsafe
    ):
        os.close(descriptor)
        raise ProductionReceiveError(
            "receiver directory permissions or ownership are unsafe"
        )
    return descriptor


def _mkdir_open_at(
    parent_fd: int,
    name: str,
    *,
    required_uid: int,
    mode: int = 0o700,
) -> int:
    try:
        os.mkdir(name, mode=mode, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ProductionReceiveError("operation directory creation failed") from exc
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise ProductionReceiveError("operation directory is unsafe") from exc
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != required_uid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        os.close(descriptor)
        raise ProductionReceiveError(
            "operation directory permissions or ownership are unsafe"
        )
    return descriptor


def _hash_plaintext_at(
    directory_fd: int,
    name: str,
    *,
    required_uid: int,
    maximum: int,
    normalize_mode: bool = True,
) -> tuple[str, int]:
    flags = (
        (os.O_RDWR if normalize_mode else os.O_RDONLY)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise ProductionReceiveError("decrypted artifact is unsafe") from exc
    try:
        initial = os.fstat(descriptor)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_uid != required_uid
            or initial.st_nlink != 1
            or not 1 <= initial.st_size <= maximum
            or (
                not normalize_mode
                and stat.S_IMODE(initial.st_mode) != 0o600
            )
        ):
            raise ProductionReceiveError("decrypted artifact is unsafe")
        if normalize_mode:
            os.fchmod(descriptor, 0o600)
        before = os.fstat(descriptor)
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise ProductionReceiveError("decrypted artifact mode is unsafe")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise ProductionReceiveError(
                    "decrypted artifact exceeded its size bound"
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise ProductionReceiveError(
                "decrypted artifact changed during verification"
            )
        os.fsync(descriptor)
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _verify_existing_plaintext_at(
    directory_fd: int,
    name: str,
    *,
    descriptor: ReceiveDescriptor,
    required_uid: int,
) -> None:
    try:
        observed_hash, observed_bytes = _hash_plaintext_at(
            directory_fd,
            name,
            required_uid=required_uid,
            maximum=MAX_PAYLOAD_BYTES,
            normalize_mode=False,
        )
    except ProductionReceiveError as exc:
        raise ProductionReceiveError(
            "artifact destination already exists with an unsafe identity"
        ) from exc
    if (
        observed_hash != descriptor.plaintext_sha256
        or observed_bytes != descriptor.plaintext_bytes
    ):
        raise ProductionReceiveError(
            "artifact destination already exists with different content"
        )


def _rename_noreplace_at(
    source_directory_fd: int,
    source_name: str,
    destination_directory_fd: int,
    destination_name: str,
) -> None:
    """Atomically publish one name without the hard-link crash window."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise ProductionReceiveError(
            "atomic create-only rename is unavailable"
        ) from exc
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_directory_fd,
        os.fsencode(source_name),
        destination_directory_fd,
        os.fsencode(destination_name),
        1,  # RENAME_NOREPLACE
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, os.strerror(error_number))
    raise ProductionReceiveError(
        "atomic create-only publication failed"
    ) from OSError(error_number, os.strerror(error_number))


def _install_plaintext_create_only(
    descriptor: ReceiveDescriptor,
    *,
    ciphertext: Path,
    operations_root: Path,
    identity_file: Path,
    required_uid: int,
) -> str:
    operations_fd = _open_secure_directory(
        operations_root,
        required_uid=required_uid,
        exact_mode=0o700,
    )
    operation_fd = incoming_fd = -1
    temporary_name = f".receive-{secrets.token_hex(16)}.tmp"
    destination_created = False
    try:
        operation_fd = _mkdir_open_at(
            operations_fd,
            descriptor.operation_id,
            required_uid=required_uid,
        )
        incoming_fd = _mkdir_open_at(
            operation_fd,
            "incoming",
            required_uid=required_uid,
        )
        try:
            os.stat(
                descriptor.destination_name,
                dir_fd=incoming_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            _verify_existing_plaintext_at(
                incoming_fd,
                descriptor.destination_name,
                descriptor=descriptor,
                required_uid=required_uid,
            )
            return "already-present"
        try:
            os.stat(temporary_name, dir_fd=incoming_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ProductionReceiveError("plaintext staging path already exists")

        _decrypt_age(
            ciphertext,
            plaintext_directory_fd=incoming_fd,
            plaintext_name=temporary_name,
            identity_file=identity_file,
            required_uid=required_uid,
        )
        observed_hash, observed_bytes = _hash_plaintext_at(
            incoming_fd,
            temporary_name,
            required_uid=required_uid,
            maximum=MAX_PAYLOAD_BYTES,
        )
        if (
            observed_hash != descriptor.plaintext_sha256
            or observed_bytes != descriptor.plaintext_bytes
        ):
            raise ProductionReceiveError("artifact plaintext hash or size differs")
        try:
            _rename_noreplace_at(
                incoming_fd,
                temporary_name,
                incoming_fd,
                descriptor.destination_name,
            )
            destination_created = True
        except FileExistsError:
            os.unlink(temporary_name, dir_fd=incoming_fd)
            _verify_existing_plaintext_at(
                incoming_fd,
                descriptor.destination_name,
                descriptor=descriptor,
                required_uid=required_uid,
            )
            return "already-present"
        os.fsync(incoming_fd)

        _verify_existing_plaintext_at(
            incoming_fd,
            descriptor.destination_name,
            descriptor=descriptor,
            required_uid=required_uid,
        )
        destination_created = False
        return "created"
    except Exception:
        try:
            os.unlink(temporary_name, dir_fd=incoming_fd)
        except (FileNotFoundError, OSError):
            pass
        if destination_created:
            try:
                os.unlink(descriptor.destination_name, dir_fd=incoming_fd)
                os.fsync(incoming_fd)
            except OSError:
                pass
        raise
    finally:
        if incoming_fd >= 0:
            os.close(incoming_fd)
        if operation_fd >= 0:
            os.close(operation_fd)
        os.close(operations_fd)


def receive_one(
    descriptor: ReceiveDescriptor,
    *,
    operations_root: Path,
    identity_file: Path,
    required_uid: int,
) -> dict[str, Any]:
    if os.geteuid() != required_uid:
        raise ProductionReceiveError(
            "receiver process owner differs from the required artifact owner"
        )
    with tempfile.TemporaryDirectory(prefix="wa-ir-production-receive-") as raw:
        staging_root = Path(raw)
        staging_root.chmod(0o700)
        ciphertext = staging_root / "payload.age"
        _download_ciphertext(descriptor, ciphertext)
        installation_result = _install_plaintext_create_only(
            descriptor,
            ciphertext=ciphertext,
            operations_root=operations_root,
            identity_file=identity_file,
            required_uid=required_uid,
        )
    return {
        "schema": ATTESTATION_SCHEMA,
        "status": "installed",
        "installation_result": installation_result,
        "operation_id": descriptor.operation_id,
        "artifact_kind": descriptor.artifact_kind,
        "destination_name": descriptor.destination_name,
        "installed_relative_path": (
            f"{descriptor.operation_id}/incoming/{descriptor.destination_name}"
        ),
        "bucket": descriptor.bucket,
        "object_key": descriptor.object_key,
        "version_id": descriptor.version_id,
        "ciphertext_sha256": descriptor.ciphertext_sha256,
        "ciphertext_bytes": descriptor.ciphertext_bytes,
        "plaintext_sha256": descriptor.plaintext_sha256,
        "plaintext_bytes": descriptor.plaintext_bytes,
        "installed_mode": "0600",
        "presigned_url_persisted": False,
        "presigned_url_logged": False,
        "archive_extracted": False,
        "docker_image_loaded": False,
        "compose_started": False,
    }


def _bootstrap_operations_root() -> Path:
    deploy_fd = _open_secure_directory(
        DEPLOY_ROOT,
        required_uid=0,
        exact_mode=None,
    )
    dark_fd = operations_fd = -1
    try:
        deploy_metadata = os.fstat(deploy_fd)
        if stat.S_IMODE(deploy_metadata.st_mode) & 0o022:
            raise ProductionReceiveError("deployment root is group/world writable")
        dark_fd = _mkdir_open_at(
            deploy_fd,
            "dark-standby",
            required_uid=0,
        )
        operations_fd = _mkdir_open_at(
            dark_fd,
            "operations",
            required_uid=0,
        )
    finally:
        if operations_fd >= 0:
            os.close(operations_fd)
        if dark_fd >= 0:
            os.close(dark_fd)
        os.close(deploy_fd)
    return OPERATIONS_ROOT


def _error_payload(message: str) -> dict[str, str]:
    return {
        "status": "blocked",
        "error": message,
        "error_class": "ProductionReceiveError",
    }


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise ProductionReceiveError(
                "receiver accepts its bounded descriptor only on stdin"
            )
        if os.geteuid() != 0:
            raise ProductionReceiveError("production receiver must run as root")
        if sys.stdin.isatty():
            raise ProductionReceiveError(
                "receiver descriptor must arrive through non-interactive stdin"
            )
        try:
            stdin_metadata = os.fstat(sys.stdin.fileno())
        except (AttributeError, OSError, ValueError) as exc:
            raise ProductionReceiveError(
                "receiver descriptor stdin is unavailable"
            ) from exc
        if not (
            stat.S_ISFIFO(stdin_metadata.st_mode)
            or stat.S_ISSOCK(stdin_metadata.st_mode)
        ):
            raise ProductionReceiveError(
                "receiver descriptor must arrive through an ephemeral stdin stream"
            )
        raw = sys.stdin.buffer.read(MAX_DESCRIPTOR_BYTES + 1)
        if len(raw) > MAX_DESCRIPTOR_BYTES:
            raise ProductionReceiveError("artifact descriptor is oversized")
        descriptor = parse_descriptor(raw)
        operations_root = _bootstrap_operations_root()
        attestation = receive_one(
            descriptor,
            operations_root=operations_root,
            identity_file=AGE_IDENTITY_FILE,
            required_uid=0,
        )
        print(json.dumps(attestation, sort_keys=True, separators=(",", ":")))
        return 0
    except ProductionReceiveError as exc:
        print(json.dumps(_error_payload(str(exc)), sort_keys=True, separators=(",", ":")))
        return 1
    except Exception:
        print(
            json.dumps(
                _error_payload("production receiver failed closed"),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
