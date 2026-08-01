#!/usr/bin/env python3
"""Receive only a sealed, encrypted Emergency WA-IR artifact set.

This is deliberately a bounded *receive* step, not an activation tool.  It
first verifies the canonical, Ed25519-signed manifest with a pinned public
key, then accepts one short-lived Arvan S3 presigned URL for each of the four
fixed artifacts.  Every URL is bound to the manifest's exact bucket, object
key and immutable VersionId.  The receiver writes each ciphertext once to its
allowlisted Emergency inbox path and verifies its exact ciphertext size and
SHA-256.  It never decrypts, loads an image, restores a database, renders an
environment, starts a container, changes Nginx, or changes a firewall.

The URL map is control-plane material: it contains no project payload and is
only valid briefly.  Artifact bytes always travel directly from the private,
versioned Arvan Object Storage endpoint to WA-IR over HTTPS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import stat
import sys
import tempfile
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from scripts import emergency_ir_object_storage_manifest as manifest


URL_MAP_SCHEMA = "gold-trade-emergency-ir-presigned-url-map-v1"
MAX_URL_MAP_BYTES = 64 * 1024
MAX_URL_BYTES = 16 * 1024
MIN_PRESIGNED_TTL_SECONDS = 60
MAX_PRESIGNED_TTL_SECONDS = 900
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DISK_HEADROOM_BYTES = 128 * 1024 * 1024
_PRESIGNED_FIELDS = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-SignedHeaders",
        "X-Amz-Signature",
        "versionId",
    }
)
_SAFE_HEX = re.compile(r"^[a-f0-9]{64}$", re.ASCII)


class EmergencyReceiverError(RuntimeError):
    """The bounded Emergency artifact receive contract was violated."""


class _RejectRedirects(HTTPRedirectHandler):
    """A presigned object request must never be redirected to another host."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        raise EmergencyReceiverError("Object Storage download was unexpectedly redirected")


def _fail(message: str) -> None:
    raise EmergencyReceiverError(message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("URL map contains a duplicate field")
        value[key] = item
    return value


def _read_root_only_regular(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    """Read one stable root-owned file without following links."""

    if os.geteuid() != 0:
        _fail("Emergency artifact receiver must run as root")
    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyReceiverError(f"{label} cannot be inspected") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o077
        or not 1 <= before.st_size <= maximum_bytes
    ):
        _fail(f"{label} must be one root-owned private regular file")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            _fail(f"{label} changed while being opened")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if len(payload) != opened.st_size or len(payload) > maximum_bytes:
            _fail(f"{label} changed while being read")
        if any(getattr(opened, field) != getattr(after, field) for field in fields):
            _fail(f"{label} changed while being read")
        return bytes(payload)
    except OSError as exc:
        raise EmergencyReceiverError(f"{label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _parse_url_map(payload: bytes, *, manifest_sha256: str) -> dict[str, str]:
    if not _SAFE_HEX.fullmatch(manifest_sha256):
        _fail("verified manifest digest is invalid")
    try:
        raw = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencyReceiverError("URL map is not strict JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema", "manifest_sha256", "artifacts"}:
        _fail("URL map fields are unsupported")
    if raw.get("schema") != URL_MAP_SCHEMA or raw.get("manifest_sha256") != manifest_sha256:
        _fail("URL map is not bound to the verified manifest")
    entries = raw.get("artifacts")
    if not isinstance(entries, list) or len(entries) != len(manifest.ARTIFACT_ORDER):
        _fail("URL map must contain the complete fixed artifact set")
    urls: dict[str, str] = {}
    for entry, expected_kind in zip(entries, manifest.ARTIFACT_ORDER, strict=True):
        if not isinstance(entry, dict) or set(entry) != {"kind", "url"}:
            _fail("URL map artifact fields are unsupported")
        kind = entry.get("kind")
        url = entry.get("url")
        if kind != expected_kind or not isinstance(url, str) or not url or len(url.encode("utf-8")) > MAX_URL_BYTES:
            _fail("URL map artifacts must be complete, unique, and in fixed order")
        urls[kind] = url
    return urls


def _validate_presigned_url(*, url: str, plan: Mapping[str, Any], artifact: Mapping[str, Any]) -> None:
    """Ensure the transient URL cannot select a different endpoint or object."""

    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise EmergencyReceiverError("Object Storage URL is malformed") from exc
    endpoint = urlsplit(str(plan["endpoint"]))
    approved_hosts = {
        endpoint.hostname,
        f"{plan['bucket']}.{endpoint.hostname}",
    }
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname not in approved_hosts
        or parsed.port is not None
        or parsed.fragment
    ):
        _fail("Object Storage URL endpoint is not allowlisted")
    object_path = quote(str(artifact["object_key"]), safe="/")
    expected_path = (
        "/" + quote(str(plan["bucket"]), safe="") + "/" + object_path
        if parsed.hostname == endpoint.hostname
        else "/" + object_path
    )
    if parsed.path != expected_path:
        _fail("Object Storage URL does not select the manifest object")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise EmergencyReceiverError("Object Storage URL query is malformed") from exc
    if not _PRESIGNED_FIELDS.issubset(query) or any(len(values) != 1 for values in query.values()):
        _fail("Object Storage URL is not one strict presigned request")
    if query["X-Amz-Algorithm"][0] != "AWS4-HMAC-SHA256":
        _fail("Object Storage URL signature algorithm is unsupported")
    if query["versionId"][0] != artifact["version_id"]:
        _fail("Object Storage URL VersionId differs from the sealed manifest")
    try:
        ttl_seconds = int(query["X-Amz-Expires"][0], 10)
    except ValueError as exc:
        raise EmergencyReceiverError("Object Storage URL expiry is invalid") from exc
    if not MIN_PRESIGNED_TTL_SECONDS <= ttl_seconds <= MAX_PRESIGNED_TTL_SECONDS:
        _fail("Object Storage URL expiry is outside the Emergency bound")


def _secure_directory(path: Path) -> None:
    """Create/check a root-controlled directory tree without accepting links."""

    if not path.is_absolute():
        _fail("artifact target directory must be absolute")
    current = Path("/")
    for component in path.parts[1:]:
        current /= component
        try:
            state = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o700)
            except OSError as exc:
                raise EmergencyReceiverError("artifact target directory cannot be created") from exc
            state = current.lstat()
        except OSError as exc:
            raise EmergencyReceiverError("artifact target directory cannot be inspected") from exc
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or stat.S_IMODE(state.st_mode) & 0o022
        ):
            _fail("artifact target directory is not root-controlled")


def _verify_existing_ciphertext(path: Path, *, expected_bytes: int, expected_hash: str) -> bool:
    """Accept a prior complete receive only when it exactly matches the seal."""

    try:
        before = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise EmergencyReceiverError("existing artifact target cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o077
        or before.st_size != expected_bytes
    ):
        _fail("existing Emergency artifact is not one sealed root-only ciphertext")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            _fail("existing Emergency artifact changed while being opened")
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            observed += len(chunk)
            if observed > expected_bytes:
                _fail("existing Emergency artifact exceeds its sealed size")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if observed != expected_bytes or digest.hexdigest() != expected_hash:
            _fail("existing Emergency artifact differs from the sealed manifest")
        if any(getattr(opened, field) != getattr(after, field) for field in fields):
            _fail("existing Emergency artifact changed while being read")
        return True
    except OSError as exc:
        raise EmergencyReceiverError("existing Emergency artifact cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_output(path: Path, *, expected_bytes: int) -> tuple[int, Path]:
    _secure_directory(path.parent)
    free_bytes = shutil.disk_usage(path.parent).free
    if free_bytes < expected_bytes + DISK_HEADROOM_BYTES:
        _fail("insufficient free disk space for the encrypted artifact")
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary = Path(f".{path.name}.{os.getpid()}.download")
    try:
        descriptor = os.open(
            temporary.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
    except Exception:
        os.close(directory_fd)
        raise
    os.close(directory_fd)
    return descriptor, path.parent / temporary


def _download_ciphertext(*, url: str, artifact: Mapping[str, Any]) -> str:
    target = Path(str(artifact["target_path"]))
    expected_bytes = int(artifact["ciphertext_bytes"])
    expected_hash = str(artifact["ciphertext_sha256"])
    if _verify_existing_ciphertext(
        target, expected_bytes=expected_bytes, expected_hash=expected_hash
    ):
        return "already-received"
    descriptor, temporary = _open_output(target, expected_bytes=expected_bytes)
    completed = False
    try:
        opener = build_opener(
            ProxyHandler({}),
            _RejectRedirects(),
            HTTPSHandler(context=ssl.create_default_context()),
        )
        request = Request(url, headers={"User-Agent": "gold-trade-emergency-ir-receiver/1"}, method="GET")
        digest = hashlib.sha256()
        size = 0
        with opener.open(request, timeout=120) as response:
            if getattr(response, "status", 200) != 200 or response.geturl() != url:
                _fail("Object Storage response differs from the sealed request")
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > expected_bytes:
                    _fail("encrypted artifact exceeds its sealed size")
                digest.update(chunk)
                written = os.write(descriptor, chunk)
                if written != len(chunk):
                    _fail("encrypted artifact write was incomplete")
        if size != expected_bytes or digest.hexdigest() != expected_hash:
            _fail("encrypted artifact hash/size differs from the sealed manifest")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            # link(2) is create-only at the final name, unlike rename/replace;
            # a concurrent or stale target can never be overwritten.
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise EmergencyReceiverError("refusing to overwrite an existing Emergency artifact") from exc
        os.unlink(temporary)
        completed = True
        return "downloaded"
    except (HTTPError, URLError, OSError) as exc:
        raise EmergencyReceiverError("Object Storage artifact download failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not completed:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _expected_bootstrap_provenance(value: object) -> dict[str, Any]:
    """Normalize the descriptor identity that authorized this receiver run."""

    try:
        return manifest.validate_bootstrap_provenance(value)
    except manifest.EmergencyManifestError as exc:
        raise EmergencyReceiverError("expected bootstrap provenance is invalid") from exc


def receive(
    *,
    manifest_path: Path,
    signing_public_key: Path,
    url_map_path: Path,
    expected_bootstrap_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify one sealed manifest then fetch only its four encrypted objects.

    The raw WA-IR bootstrap supplies an independently authenticated descriptor
    identity.  A manifest signed by the pinned bundle key is still rejected if
    it was made for a different source revision, bundle digest, or key ID.
    """

    expected = _expected_bootstrap_provenance(expected_bootstrap_provenance)
    raw_manifest = _read_root_only_regular(
        manifest_path, label="sealed Emergency manifest", maximum_bytes=manifest.MAX_MANIFEST_BYTES
    )
    public_key = manifest.load_public_key(signing_public_key)
    verified = manifest.verify_manifest_bytes(raw_manifest, public_key=public_key)
    plan = verified.as_receive_plan()
    if plan["bootstrap_provenance"] != expected:
        _fail("sealed manifest bootstrap provenance differs from the controller descriptor")
    urls = _parse_url_map(
        _read_root_only_regular(url_map_path, label="Emergency presigned URL map", maximum_bytes=MAX_URL_MAP_BYTES),
        manifest_sha256=plan["manifest_sha256"],
    )
    received: list[dict[str, str]] = []
    for artifact in plan["artifacts"]:
        kind = str(artifact["kind"])
        _validate_presigned_url(url=urls[kind], plan=plan, artifact=artifact)
        receive_state = _download_ciphertext(url=urls[kind], artifact=artifact)
        received.append(
            {
                "kind": kind,
                "target_path": str(artifact["target_path"]),
                "ciphertext_sha256": str(artifact["ciphertext_sha256"]),
                "receive_state": receive_state,
            }
        )
    return {
        "status": "received-non-authorizing",
        "campaign_id": plan["campaign_id"],
        "manifest_sha256": plan["manifest_sha256"],
        "artifacts": received,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--signing-public-key", type=Path, required=True)
    parser.add_argument("--url-map", type=Path, required=True)
    parser.add_argument("--expected-publisher-source-revision", required=True)
    parser.add_argument("--expected-receiver-bundle-sha256", required=True)
    parser.add_argument("--expected-receiver-bundle-bytes", type=int, required=True)
    parser.add_argument("--expected-signer-key-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = receive(
            manifest_path=args.manifest,
            signing_public_key=args.signing_public_key,
            url_map_path=args.url_map,
            expected_bootstrap_provenance={
                "schema": manifest.BOOTSTRAP_PROVENANCE_SCHEMA,
                "publisher_source_revision": args.expected_publisher_source_revision,
                "receiver_bundle_sha256": args.expected_receiver_bundle_sha256,
                "receiver_bundle_bytes": args.expected_receiver_bundle_bytes,
                "signer_key_id": args.expected_signer_key_id,
            },
        )
    except EmergencyReceiverError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
