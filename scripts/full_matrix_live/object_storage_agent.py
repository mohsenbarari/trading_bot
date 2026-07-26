#!/usr/bin/env python3
"""Poll and execute authenticated Full Matrix requests on disposable WA-IR."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


from object_storage_protocol import (
    ObjectStorageProtocolError,
    build_response,
    canonical_bytes,
    public_key_id,
    strict_object,
    verify_request,
)


CONFIG_SCHEMA = "three-site-full-matrix-object-storage-agent-config-v1"
ARVAN_HOST = "s3.ir-thr-at1.arvanstorage.ir"
CONFIG_PATH = Path("/etc/trading-bot-full-matrix/object-storage-agent.json")
AGE_IDENTITY = Path("/etc/trading-bot-full-matrix/agent-age-identity.txt")
AGENT_SIGNING_KEY = Path("/etc/trading-bot-full-matrix/agent-ed25519.pem")
STATE_ROOT = Path("/var/lib/trading-bot-full-matrix/object-storage-agent")
STATE_FILE = STATE_ROOT / "state.json"
WORK_ROOT = Path("/run/trading-bot-full-matrix/object-storage-agent")
REPO_ROOT = Path("/srv/trading-bot-three-site/current")
SITE_AGENT = Path(
    "/usr/local/lib/trading-bot-full-matrix/site_agent.py"
)
MAX_CIPHERTEXT_BYTES = 3 * 1024 * 1024
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
}


class ObjectStorageAgentError(RuntimeError):
    """The pull agent failed closed."""


def _safe_json(path: Path, *, label: str, mode: int = 0o600) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink():
        raise ObjectStorageAgentError(f"{label} path is unsafe")
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
            or stat.S_IMODE(metadata.st_mode) != mode
            or not 2 <= metadata.st_size <= MAX_CIPHERTEXT_BYTES
        ):
            raise ObjectStorageAgentError(f"{label} is unsafe")
        raw = os.pread(descriptor, metadata.st_size + 1, 0)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw, object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObjectStorageAgentError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ObjectStorageAgentError(f"{label} is not an object")
    return value


def _validate_url(value: Any, *, method: str) -> str:
    if not isinstance(value, str) or len(value) > 8192:
        raise ObjectStorageAgentError("presigned URL is invalid")
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise ObjectStorageAgentError("presigned URL is malformed") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not (
            hostname == ARVAN_HOST
            or hostname.endswith("." + ARVAN_HOST)
        )
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or "X-Amz-Signature=" not in parsed.query
        or method not in {"GET", "PUT"}
    ):
        raise ObjectStorageAgentError("presigned URL is outside the fixed Arvan endpoint")
    return value


def load_config(path: Path) -> dict[str, Any]:
    value = _safe_json(path, label="Object Storage agent config")
    fields = {
        "schema",
        "role",
        "campaign_id",
        "release_sha",
        "request_url",
        "response_url",
        "controller_public_key",
        "controller_age_recipient",
        "poll_interval_seconds",
    }
    if (
        set(value) != fields
        or value.get("schema") != CONFIG_SCHEMA
        or value.get("role") != "webapp_ir"
        or not isinstance(value.get("campaign_id"), str)
        or not isinstance(value.get("release_sha"), str)
        or value.get("poll_interval_seconds") not in {5, 10, 15}
    ):
        raise ObjectStorageAgentError("Object Storage agent config is invalid")
    value["request_url"] = _validate_url(value["request_url"], method="GET")
    value["response_url"] = _validate_url(value["response_url"], method="PUT")
    try:
        controller_key = base64.b64decode(
            value["controller_public_key"], validate=True
        )
    except (ValueError, TypeError) as exc:
        raise ObjectStorageAgentError("controller public key is malformed") from exc
    if (
        len(controller_key) != 32
        or not str(value["controller_age_recipient"]).startswith("age1")
    ):
        raise ObjectStorageAgentError("controller public material is invalid")
    return value


def _private_file(path: Path, *, label: str) -> bytes:
    if path.is_symlink():
        raise ObjectStorageAgentError(f"{label} path is unsafe")
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
            or not 1 <= metadata.st_size <= 16 * 1024
        ):
            raise ObjectStorageAgentError(f"{label} is unsafe")
        return os.pread(descriptor, metadata.st_size + 1, 0)
    finally:
        os.close(descriptor)


def _load_signing_key() -> tuple[Ed25519PrivateKey, str]:
    try:
        key = serialization.load_pem_private_key(
            _private_file(AGENT_SIGNING_KEY, label="agent signing key"),
            password=None,
        )
    except (ValueError, TypeError) as exc:
        raise ObjectStorageAgentError("agent signing key is invalid") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ObjectStorageAgentError("agent signing key type is invalid")
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return key, public_key_id(public)


def _download(url: str) -> bytes | None:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "trading-bot-full-matrix-agent/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_CIPHERTEXT_BYTES:
                raise ObjectStorageAgentError("request object exceeds its fixed bound")
            raw = response.read(MAX_CIPHERTEXT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise ObjectStorageAgentError("request Object Storage GET failed") from exc
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise ObjectStorageAgentError("request Object Storage GET failed") from exc
    if not 2 <= len(raw) <= MAX_CIPHERTEXT_BYTES:
        raise ObjectStorageAgentError("request ciphertext size is invalid")
    return raw


def _put(url: str, raw: bytes) -> None:
    request = urllib.request.Request(
        url,
        data=raw,
        method="PUT",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(raw)),
            "User-Agent": "trading-bot-full-matrix-agent/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status not in {200, 201, 204}:
                raise ObjectStorageAgentError("response Object Storage PUT was rejected")
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise ObjectStorageAgentError("response Object Storage PUT failed") from exc


def _age(argv: list[str], *, timeout: int = 120) -> None:
    age = shutil.which("age", path="/usr/bin:/bin")
    if age != "/usr/bin/age":
        raise ObjectStorageAgentError("pinned age executable is unavailable")
    result = subprocess.run(
        [age, *argv],
        cwd=WORK_ROOT,
        env=SAFE_ENV,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise ObjectStorageAgentError("age operation failed closed")


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"last_sequence": 0, "last_ciphertext_sha256": ""}
    value = _safe_json(STATE_FILE, label="Object Storage agent state")
    if (
        set(value) != {"last_sequence", "last_ciphertext_sha256"}
        or type(value["last_sequence"]) is not int
        or value["last_sequence"] < 0
        or (
            value["last_ciphertext_sha256"]
            and len(value["last_ciphertext_sha256"]) != 64
        )
    ):
        raise ObjectStorageAgentError("Object Storage agent state is invalid")
    return value


def _write_atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise ObjectStorageAgentError("atomic write failed")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _site_execute(request_path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "/usr/bin/python3",
            "-I",
            "-B",
            str(SITE_AGENT),
            "--request",
            str(request_path),
        ],
        cwd=REPO_ROOT,
        env=SAFE_ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=7200,
    )
    if result.returncode != 0 or result.stderr or not 2 <= len(result.stdout) <= 2 * 1024 * 1024:
        raise ObjectStorageAgentError("closed site agent failed")
    try:
        value = json.loads(result.stdout, object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObjectStorageAgentError("closed site agent output is invalid") from exc
    if not isinstance(value, dict) or value.get("status") != "passed":
        raise ObjectStorageAgentError("closed site agent did not pass")
    return value


def process_once(config: dict[str, Any]) -> dict[str, Any]:
    WORK_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(WORK_ROOT, 0o700)
    ciphertext = _download(config["request_url"])
    if ciphertext is None:
        return {"status": "idle", "reason": "request_missing"}
    ciphertext_sha = hashlib.sha256(ciphertext).hexdigest()
    state = _load_state()
    if ciphertext_sha == state["last_ciphertext_sha256"]:
        return {"status": "idle", "reason": "request_already_processed"}
    signing_key, agent_key_id = _load_signing_key()
    _private_file(AGE_IDENTITY, label="agent age identity")
    with tempfile.TemporaryDirectory(dir=WORK_ROOT, prefix="request-") as raw_dir:
        work = Path(raw_dir)
        encrypted_request = work / "request.json.age"
        request_path = work / "request.json"
        _write_atomic(encrypted_request, ciphertext)
        _age(
            [
                "--decrypt",
                "--identity",
                str(AGE_IDENTITY),
                "--output",
                str(request_path),
                str(encrypted_request),
            ]
        )
        os.chmod(request_path, 0o600)
        request = _safe_json(request_path, label="decrypted control request")
        verified = verify_request(
            request,
            controller_public_key_b64=config["controller_public_key"],
            expected_release_sha=config["release_sha"],
            expected_campaign_id=config["campaign_id"],
            minimum_sequence=int(state["last_sequence"]) + 1,
            now=datetime.now(timezone.utc),
        )
        request_sha = hashlib.sha256(canonical_bytes(verified)).hexdigest()
        try:
            site_result = _site_execute(request_path)
            response_status = "passed"
            response_result: dict[str, Any] = site_result
        except Exception as exc:
            response_status = "failed"
            response_result = {
                "error_class": type(exc).__name__,
                "retryable": False,
            }
        response = build_response(
            private_key=signing_key,
            agent_key_id=agent_key_id,
            request=verified,
            request_sha256=request_sha,
            status=response_status,
            result=response_result,
            completed_at=datetime.now(timezone.utc),
        )
        response_path = work / "response.json"
        encrypted_response = work / "response.json.age"
        _write_atomic(response_path, canonical_bytes(response) + b"\n")
        _age(
            [
                "--encrypt",
                "--recipient",
                config["controller_age_recipient"],
                "--output",
                str(encrypted_response),
                str(response_path),
            ]
        )
        response_ciphertext = encrypted_response.read_bytes()
        if not 2 <= len(response_ciphertext) <= MAX_CIPHERTEXT_BYTES:
            raise ObjectStorageAgentError("encrypted response size is invalid")
        _put(config["response_url"], response_ciphertext)
    _write_atomic(
        STATE_FILE,
        canonical_bytes(
            {
                "last_sequence": verified["sequence"],
                "last_ciphertext_sha256": ciphertext_sha,
            }
        )
        + b"\n",
    )
    return {
        "status": "processed",
        "sequence": verified["sequence"],
        "request_id": verified["request_id"],
        "operation": verified["operation"],
        "result_status": response_status,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.once:
        print(json.dumps(process_once(config), sort_keys=True))
        return 0
    while True:
        try:
            process_once(config)
        except Exception:
            # systemd owns restart/backoff; no request or response material is logged.
            return 1
        time.sleep(int(config["poll_interval_seconds"]))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ObjectStorageAgentError, ObjectStorageProtocolError, OSError, RuntimeError):
        raise SystemExit(1)
