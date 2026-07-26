#!/usr/bin/env python3
"""Provision and use the authenticated WA-IR Full Matrix pull channel."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_text  # noqa: E402
from scripts.full_matrix_live.object_storage_protocol import (  # noqa: E402
    OPERATIONS,
    ObjectStorageProtocolError,
    build_request,
    canonical_bytes,
    public_key_b64,
    public_key_id,
    strict_object,
    verify_response,
)
from scripts.publish_wa_ir_object_storage_preflight import (  # noqa: E402
    ARVAN_ENDPOINT,
    ARVAN_REGION,
    _client,
    require_private_versioned_bucket,
)
from scripts.verify_three_site_staging_inventory import PRODUCTION_BUCKETS  # noqa: E402


CONFIG_SCHEMA = "three-site-full-matrix-object-storage-controller-config-v1"
AGENT_CONFIG_SCHEMA = "three-site-full-matrix-object-storage-agent-config-v1"
DEFAULT_CREDENTIALS = Path(
    "/root/secure-envs/trading-bot/"
    "three-site-staging-3138d0c2-dc32d903/secrets/staging-dr-blob-s3.json"
)
DEFAULT_AGENT_RECIPIENT = Path(
    "/root/secure-envs/arvan/"
    "full-matrix-destructive-20260726.webapp_ir.age-recipient"
)
MAX_CONTROL_BYTES = 3 * 1024 * 1024
SHA40_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_ARTIFACT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,190}\Z")
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
AGE_RECIPIENT_RE = re.compile(r"age1[0-9a-z]{40,80}\Z")
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
}


class ObjectStorageControllerError(RuntimeError):
    """The controller channel failed closed."""


def _external_anchor_head(artifacts: list[dict[str, Any]]) -> str:
    if not artifacts or len(artifacts) > 16_384:
        raise ObjectStorageControllerError("external anchor artifact set is invalid")
    previous_name = ""
    head = ""
    for item in artifacts:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "size"}
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
            or not SAFE_ARTIFACT_NAME_RE.fullmatch(str(item.get("path") or ""))
            or not SHA256_RE.fullmatch(str(item.get("sha256") or ""))
            or type(item.get("size")) is not int
            or not 2 <= item["size"] <= 32 * 1024 * 1024
            or (previous_name and item["path"] <= previous_name)
        ):
            raise ObjectStorageControllerError("external anchor artifact record is invalid")
        previous_name = item["path"]
        head = hashlib.sha256(f"{head}:{item['sha256']}".encode("ascii")).hexdigest()
    return head


def store_external_anchor(
    config_path: Path,
    *,
    campaign_id: str,
    release_sha: str,
    execution_class: str,
    operation_id: str,
    artifacts: list[dict[str, Any]],
    client=None,  # noqa: ANN001
) -> dict[str, Any]:
    """Store and version-read-back the ordered campaign evidence-chain head.

    The only externally supplied values are already-retained artifact names,
    digests, and sizes.  Object key selection, bucket, encryption and
    read-back validation stay source-owned.
    """

    if (
        not UUID_RE.fullmatch(campaign_id)
        or SHA40_RE.fullmatch(release_sha) is None
        or execution_class not in {"shared-host-safe", "dedicated-host-destructive"}
        or not UUID_RE.fullmatch(operation_id)
    ):
        raise ObjectStorageControllerError("external anchor identity is invalid")
    config = load_controller_config(config_path)
    if config["campaign_id"] != campaign_id or config["release_sha"] != release_sha:
        raise ObjectStorageControllerError("external anchor controller binding differs")
    chain_head = _external_anchor_head(artifacts)
    prefix = str(config["request_key"]).rsplit("/", 1)[0]
    if not prefix or any(part in {"", ".", ".."} for part in prefix.split("/")):
        raise ObjectStorageControllerError("external anchor Object Storage prefix is unsafe")
    payload = {
        "schema": "three-site-full-matrix-artifact-anchor-v1",
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "execution_class": execution_class,
        "operation_id": operation_id,
        "artifacts": artifacts,
        "chain_head": chain_head,
    }
    encoded = canonical_bytes(payload)
    anchor_sha256 = hashlib.sha256(encoded).hexdigest()
    key = f"{prefix}/external-anchors/{operation_id}-{chain_head}.json"
    active_client = client or _client(_credentials(Path(config["credentials_file"])))
    require_private_versioned_bucket(active_client, bucket=config["bucket"])
    try:
        put = active_client.put_object(
            Bucket=config["bucket"],
            Key=key,
            Body=encoded,
            ContentLength=len(encoded),
            ContentType="application/json",
            Metadata={"sha256": anchor_sha256, "chain-head": chain_head},
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
        )
        put_version = str(put.get("VersionId") or "")
    except Exception as exc:
        code = ""
        if hasattr(exc, "response"):
            code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code not in {"PreconditionFailed", "412"}:
            raise
        put_version = ""
    read_back = active_client.get_object(Bucket=config["bucket"], Key=key)
    body = read_back.get("Body")
    if body is None or not hasattr(body, "read"):
        raise ObjectStorageControllerError("external anchor read-back body is invalid")
    observed = body.read(2 * 1024 * 1024 + 1)
    version_id = str(read_back.get("VersionId") or put_version)
    if (
        observed != encoded
        or len(observed) > 2 * 1024 * 1024
        or not version_id
        or (read_back.get("Metadata") or {}).get("sha256") != anchor_sha256
        or (read_back.get("Metadata") or {}).get("chain-head") != chain_head
    ):
        raise ObjectStorageControllerError("external anchor versioned read-back differs")
    return {
        "status": "anchored",
        "object_key": key,
        "object_version_id": version_id,
        "anchor_sha256": anchor_sha256,
        "chain_head": chain_head,
        "artifact_count": len(artifacts),
    }


def _private_regular(
    path: Path,
    *,
    label: str,
    max_size: int = MAX_CONTROL_BYTES,
) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise ObjectStorageControllerError(f"{label} path is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= max_size
        ):
            raise ObjectStorageControllerError(f"{label} is unsafe")
        return os.pread(descriptor, metadata.st_size + 1, 0)
    finally:
        os.close(descriptor)


def _safe_json(path: Path, *, label: str) -> dict[str, Any]:
    raw = _private_regular(path, label=label)
    try:
        value = json.loads(raw, object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObjectStorageControllerError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ObjectStorageControllerError(f"{label} is not an object")
    return value


def _write_new(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    if not path.is_absolute() or path.is_symlink():
        raise ObjectStorageControllerError("output path is unsafe")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        mode,
    )
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise ObjectStorageControllerError("secure write was incomplete")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    _write_new(temporary, raw)
    os.replace(temporary, path)


def _credentials(path: Path) -> tuple[str, str, str, str]:
    value = _safe_json(path, label="Object Storage credential file")
    if not isinstance(value, dict) or set(value) != {"access_key", "secret_key"}:
        raise ObjectStorageControllerError("Object Storage credential fields are invalid")
    access = str(value["access_key"])
    secret = str(value["secret_key"])
    if len(access) < 8 or len(secret) < 32:
        raise ObjectStorageControllerError("Object Storage credentials are malformed")
    return access, secret, ARVAN_ENDPOINT, ARVAN_REGION


def _age_recipient(path: Path, *, label: str) -> str:
    value = _private_regular(path, label=label, max_size=4096).decode().strip()
    if AGE_RECIPIENT_RE.fullmatch(value) is None:
        raise ObjectStorageControllerError(f"{label} is malformed")
    return value


def _generate_age_identity(identity: Path, recipient: Path) -> None:
    age_keygen = shutil.which("age-keygen", path="/usr/bin:/bin")
    if age_keygen != "/usr/bin/age-keygen":
        raise ObjectStorageControllerError("pinned age-keygen is unavailable")
    result = subprocess.run(
        [age_keygen, "-o", str(identity)],
        cwd=identity.parent,
        env=SAFE_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        identity.unlink(missing_ok=True)
        raise ObjectStorageControllerError("controller age identity generation failed")
    os.chmod(identity, 0o600)
    public_result = subprocess.run(
        [age_keygen, "-y", str(identity)],
        cwd=identity.parent,
        env=SAFE_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if public_result.returncode != 0:
        raise ObjectStorageControllerError("controller age recipient derivation failed")
    _write_new(recipient, (public_result.stdout.strip() + "\n").encode())


def _pem(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _raw_public(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _presigned(client: Any, *, operation: str, bucket: str, key: str, ttl: int) -> str:
    return client.generate_presigned_url(
        operation,
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl,
        HttpMethod="GET" if operation == "get_object" else "PUT",
    )


def provision(args: argparse.Namespace) -> dict[str, Any]:
    if (
        args.output_root.exists()
        or args.output_root.is_symlink()
        or not SHA40_RE.fullmatch(args.release_sha)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{5,190}", args.campaign_id)
        or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", args.bucket)
        or args.bucket in PRODUCTION_BUCKETS
        or not re.fullmatch(r"[a-z0-9][a-z0-9/_.-]{8,180}", args.prefix)
        or ".." in Path(args.prefix).parts
        or not 3600 <= args.url_ttl_seconds <= 604800
    ):
        raise ObjectStorageControllerError("Object Storage channel provision input is invalid")
    _private_regular(args.credentials, label="Object Storage credential file")
    agent_recipient = _age_recipient(
        args.agent_age_recipient,
        label="WA-IR agent age recipient",
    )
    args.output_root.mkdir(mode=0o700, parents=True)
    os.chmod(args.output_root, 0o700)
    controller_age_identity = args.output_root / "controller-age-identity.txt"
    controller_age_recipient = args.output_root / "controller-age-recipient.txt"
    _generate_age_identity(controller_age_identity, controller_age_recipient)
    controller_age_public = _age_recipient(
        controller_age_recipient,
        label="controller age recipient",
    )
    controller_signing = Ed25519PrivateKey.generate()
    agent_signing = Ed25519PrivateKey.generate()
    controller_signing_path = args.output_root / "controller-ed25519.pem"
    agent_signing_path = args.output_root / "agent-ed25519.pem"
    _write_new(controller_signing_path, _pem(controller_signing))
    _write_new(agent_signing_path, _pem(agent_signing))
    controller_public = _raw_public(controller_signing)
    agent_public = _raw_public(agent_signing)
    client = _client(_credentials(args.credentials))
    require_private_versioned_bucket(client, bucket=args.bucket)
    request_key = f"{args.prefix.strip('/')}/request.age"
    response_key = f"{args.prefix.strip('/')}/response.age"
    agent_config = {
        "schema": AGENT_CONFIG_SCHEMA,
        "role": "webapp_ir",
        "campaign_id": args.campaign_id,
        "release_sha": args.release_sha,
        "request_url": _presigned(
            client,
            operation="get_object",
            bucket=args.bucket,
            key=request_key,
            ttl=args.url_ttl_seconds,
        ),
        "response_url": _presigned(
            client,
            operation="put_object",
            bucket=args.bucket,
            key=response_key,
            ttl=args.url_ttl_seconds,
        ),
        "controller_public_key": public_key_b64(controller_public),
        "controller_age_recipient": controller_age_public,
        "poll_interval_seconds": 5,
    }
    agent_config_path = args.output_root / "agent-config.json"
    _write_new(agent_config_path, canonical_bytes(agent_config) + b"\n")
    controller_config = {
        "schema": CONFIG_SCHEMA,
        "role": "webapp_ir",
        "campaign_id": args.campaign_id,
        "release_sha": args.release_sha,
        "bucket": args.bucket,
        "request_key": request_key,
        "response_key": response_key,
        "credentials_file": str(args.credentials),
        "controller_signing_key": str(controller_signing_path),
        "controller_age_identity": str(controller_age_identity),
        "agent_age_recipient": agent_recipient,
        "agent_public_key": public_key_b64(agent_public),
        "state_file": str(args.output_root / "controller-state.json"),
        "lock_file": str(args.output_root / "controller.lock"),
    }
    controller_config_path = args.output_root / "controller-config.json"
    _write_new(controller_config_path, canonical_bytes(controller_config) + b"\n")
    _write_new(
        args.output_root / "controller-state.json",
        canonical_bytes({"last_sequence": 0, "pending": None}) + b"\n",
    )
    _write_new(args.output_root / "controller.lock", b"locked-by-flock\n")
    return {
        "status": "provisioned",
        "controller_config": str(controller_config_path),
        "agent_config": str(agent_config_path),
        "agent_signing_key": str(agent_signing_path),
        "controller_public_key_id": public_key_id(controller_public),
        "agent_public_key_id": public_key_id(agent_public),
        "bucket": args.bucket,
        "request_key": request_key,
        "response_key": response_key,
        "url_ttl_seconds": args.url_ttl_seconds,
        "production_touched": False,
        "delete_operation_available": False,
    }


def load_controller_config(path: Path) -> dict[str, Any]:
    value = _safe_json(path, label="Object Storage controller config")
    fields = {
        "schema",
        "role",
        "campaign_id",
        "release_sha",
        "bucket",
        "request_key",
        "response_key",
        "credentials_file",
        "controller_signing_key",
        "controller_age_identity",
        "agent_age_recipient",
        "agent_public_key",
        "state_file",
        "lock_file",
    }
    if (
        set(value) != fields
        or value.get("schema") != CONFIG_SCHEMA
        or value.get("role") != "webapp_ir"
        or value.get("bucket") in PRODUCTION_BUCKETS
        or not SHA40_RE.fullmatch(str(value.get("release_sha") or ""))
        or not re.fullmatch(
            r"[a-z0-9][a-z0-9/_.-]{8,220}",
            str(value.get("request_key") or ""),
        )
        or not re.fullmatch(
            r"[a-z0-9][a-z0-9/_.-]{8,220}",
            str(value.get("response_key") or ""),
        )
        or value.get("request_key") == value.get("response_key")
    ):
        raise ObjectStorageControllerError("Object Storage controller config is invalid")
    for name in (
        "credentials_file",
        "controller_signing_key",
        "controller_age_identity",
        "state_file",
        "lock_file",
    ):
        if not Path(str(value[name])).is_absolute():
            raise ObjectStorageControllerError("controller config contains a relative path")
    _private_regular(Path(value["credentials_file"]), label="Object Storage credential file")
    _private_regular(Path(value["controller_signing_key"]), label="controller signing key")
    _private_regular(Path(value["controller_age_identity"]), label="controller age identity")
    _private_regular(Path(value["state_file"]), label="controller state")
    _private_regular(Path(value["lock_file"]), label="controller lock")
    if AGE_RECIPIENT_RE.fullmatch(str(value.get("agent_age_recipient") or "")) is None:
        raise ObjectStorageControllerError("agent age recipient is invalid")
    try:
        agent_public = base64.b64decode(value["agent_public_key"], validate=True)
    except (ValueError, TypeError) as exc:
        raise ObjectStorageControllerError("agent public key is malformed") from exc
    if len(agent_public) != 32:
        raise ObjectStorageControllerError("agent public key length is invalid")
    return value


def _load_signing_key(path: Path) -> tuple[Ed25519PrivateKey, str]:
    try:
        key = serialization.load_pem_private_key(
            _private_regular(path, label="controller signing key"),
            password=None,
        )
    except (ValueError, TypeError) as exc:
        raise ObjectStorageControllerError("controller signing key is invalid") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ObjectStorageControllerError("controller signing key type is invalid")
    public = _raw_public(key)
    return key, public_key_id(public)


def _run_age(argv: list[str], *, cwd: Path) -> None:
    age = shutil.which("age", path="/usr/bin:/bin")
    if age != "/usr/bin/age":
        raise ObjectStorageControllerError("pinned age executable is unavailable")
    result = subprocess.run(
        [age, *argv],
        cwd=cwd,
        env=SAFE_ENV,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        raise ObjectStorageControllerError("age operation failed closed")


def _object_identity(client: Any, *, bucket: str, key: str) -> tuple[str, str]:
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except client.exceptions.ClientError as exc:
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        if status == 404:
            return "", ""
        raise
    return str(response.get("ETag") or ""), str(response.get("VersionId") or "")


def dispatch(
    config_path: Path,
    *,
    operation: str,
    context: Mapping[str, Any],
    attempt: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    config = load_controller_config(config_path)
    if (
        operation not in OPERATIONS
        or type(attempt) is not int
        or attempt < 1
        or not 10 <= timeout_seconds <= 7200
    ):
        raise ObjectStorageControllerError("Object Storage dispatch input is invalid")
    lock_path = Path(config["lock_file"])
    lock_descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
    try:
        state = _safe_json(Path(config["state_file"]), label="controller state")
        if (
            set(state) != {"last_sequence", "pending"}
            or type(state["last_sequence"]) is not int
            or state["last_sequence"] < 0
            or (state["pending"] is not None and not isinstance(state["pending"], dict))
        ):
            raise ObjectStorageControllerError("controller state is invalid")
        signing_key, controller_key_id = _load_signing_key(
            Path(config["controller_signing_key"])
        )
        client = _client(_credentials(Path(config["credentials_file"])))
        require_private_versioned_bucket(client, bucket=config["bucket"])
        context_sha = hashlib.sha256(canonical_bytes(context)).hexdigest()
        current_response = _object_identity(
            client, bucket=config["bucket"], key=config["response_key"]
        )
        pending = state["pending"]
        if pending is not None:
            pending_fields = {
                "operation",
                "attempt",
                "context_sha256",
                "request",
                "request_sha256",
                "baseline_response",
            }
            if (
                set(pending) != pending_fields
                or pending.get("operation") != operation
                or pending.get("attempt") != attempt
                or pending.get("context_sha256") != context_sha
                or not isinstance(pending.get("request"), dict)
                or not isinstance(pending.get("baseline_response"), list)
                or len(pending["baseline_response"]) != 2
            ):
                raise ObjectStorageControllerError(
                    "a different Object Storage operation is already pending"
                )
            request = dict(pending["request"])
            request_raw = canonical_bytes(request)
            request_sha = hashlib.sha256(request_raw).hexdigest()
            if request_sha != pending["request_sha256"]:
                raise ObjectStorageControllerError("pending control request differs")
            sequence = int(request["sequence"])
            before_response = tuple(str(item) for item in pending["baseline_response"])
            try:
                request_expiry = datetime.fromisoformat(str(request["expires_at"]))
            except ValueError as exc:
                raise ObjectStorageControllerError(
                    "pending request expiration is invalid"
                ) from exc
            # A response PUT is committed before the agent advances its local
            # sequence.  If the baseline object is still current after expiry,
            # it is safe to re-sign the same sequence with a new request ID.
            if (
                current_response == before_response
                and datetime.now(timezone.utc) >= request_expiry
            ):
                pending = None
        if pending is None:
            sequence = state["last_sequence"] + 1
            now = datetime.now(timezone.utc)
            request = build_request(
                private_key=signing_key,
                controller_key_id=controller_key_id,
                request_id=str(uuid.uuid4()),
                campaign_id=config["campaign_id"],
                release_sha=config["release_sha"],
                sequence=sequence,
                attempt=attempt,
                operation=operation,
                context=context,
                issued_at=now,
                expires_at=now + timedelta(seconds=min(600, timeout_seconds)),
            )
            request_raw = canonical_bytes(request)
            request_sha = hashlib.sha256(request_raw).hexdigest()
            before_response = current_response
            state["pending"] = {
                "operation": operation,
                "attempt": attempt,
                "context_sha256": context_sha,
                "request": request,
                "request_sha256": request_sha,
                "baseline_response": list(before_response),
            }
            _write_atomic(
                Path(config["state_file"]),
                canonical_bytes(state) + b"\n",
            )
        with tempfile.TemporaryDirectory(prefix="full-matrix-object-controller-") as raw:
            work = Path(raw)
            request_path = work / "request.json"
            encrypted_request = work / "request.json.age"
            request_path.write_bytes(request_raw + b"\n")
            os.chmod(request_path, 0o600)
            _run_age(
                [
                    "--encrypt",
                    "--recipient",
                    config["agent_age_recipient"],
                    "--output",
                    str(encrypted_request),
                    str(request_path),
                ],
                cwd=work,
            )
            ciphertext = encrypted_request.read_bytes()
            if not 2 <= len(ciphertext) <= MAX_CONTROL_BYTES:
                raise ObjectStorageControllerError("encrypted request size is invalid")
            if current_response != before_response and all(current_response):
                # A prior invocation reached the durable response PUT and then
                # lost controller progress. Consume it without republishing.
                request_version = "recovered-pending-request"
                response_identity = current_response
            else:
                published = client.put_object(
                    Bucket=config["bucket"],
                    Key=config["request_key"],
                    Body=ciphertext,
                    ContentType="application/octet-stream",
                    Metadata={
                        "kind": "full-matrix-control-request",
                        "request-sha256": request_sha,
                        "sequence": str(sequence),
                    },
                )
                request_version = str(published.get("VersionId") or "")
                deadline = time.monotonic() + timeout_seconds
                response_identity = before_response
                while time.monotonic() < deadline:
                    time.sleep(2)
                    response_identity = _object_identity(
                        client,
                        bucket=config["bucket"],
                        key=config["response_key"],
                    )
                    if response_identity != before_response and all(response_identity):
                        break
                else:
                    raise ObjectStorageControllerError("WA-IR control response timed out")
            response_object = client.get_object(
                Bucket=config["bucket"],
                Key=config["response_key"],
                VersionId=response_identity[1],
            )
            response_ciphertext = response_object["Body"].read(MAX_CONTROL_BYTES + 1)
            if not 2 <= len(response_ciphertext) <= MAX_CONTROL_BYTES:
                raise ObjectStorageControllerError("encrypted response size is invalid")
            encrypted_response = work / "response.json.age"
            response_path = work / "response.json"
            encrypted_response.write_bytes(response_ciphertext)
            os.chmod(encrypted_response, 0o600)
            _run_age(
                [
                    "--decrypt",
                    "--identity",
                    config["controller_age_identity"],
                    "--output",
                    str(response_path),
                    str(encrypted_response),
                ],
                cwd=work,
            )
            os.chmod(response_path, 0o600)
            response = _safe_json(response_path, label="decrypted WA-IR response")
        verified = verify_response(
            response,
            agent_public_key_b64=config["agent_public_key"],
            request=request,
            request_sha256=request_sha,
        )
        _write_atomic(
            Path(config["state_file"]),
            canonical_bytes({"last_sequence": sequence, "pending": None}) + b"\n",
        )
        if verified["status"] != "passed":
            raise ObjectStorageControllerError("WA-IR closed site operation failed")
        return {
            "status": "passed",
            "role": "webapp_ir",
            "request_id": request["request_id"],
            "sequence": sequence,
            "operation": operation,
            "request_sha256": request_sha,
            "request_version_id": request_version,
            "response_version_id": response_identity[1],
            "result": verified["result"],
            "transport": "private-versioned-object-storage-pull",
            "production_touched": False,
        }
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    provision_parser = subparsers.add_parser("provision")
    provision_parser.add_argument("--output-root", type=Path, required=True)
    provision_parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    provision_parser.add_argument(
        "--agent-age-recipient",
        type=Path,
        default=DEFAULT_AGENT_RECIPIENT,
    )
    provision_parser.add_argument("--campaign-id", required=True)
    provision_parser.add_argument("--release-sha", required=True)
    provision_parser.add_argument("--bucket", required=True)
    provision_parser.add_argument("--prefix", required=True)
    provision_parser.add_argument("--url-ttl-seconds", type=int, default=604800)
    dispatch_parser = subparsers.add_parser("dispatch")
    dispatch_parser.add_argument("--config", type=Path, required=True)
    dispatch_parser.add_argument("--operation", choices=sorted(OPERATIONS), required=True)
    dispatch_parser.add_argument("--context", type=Path, required=True)
    dispatch_parser.add_argument("--attempt", type=int, required=True)
    dispatch_parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if args.action == "provision":
        result = provision(args)
    else:
        context = _safe_json(args.context, label="Object Storage dispatch context")
        result = dispatch(
            args.config,
            operation=args.operation,
            context=context,
            attempt=args.attempt,
            timeout_seconds=args.timeout_seconds,
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ObjectStorageControllerError, ObjectStorageProtocolError, OSError, RuntimeError):
        raise SystemExit(1)
