#!/usr/bin/env python3
"""Bootstrap and execute WA-IR preflight without transferring payloads over SSH."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import stat
import subprocess
from typing import Any

from core.secure_file_io import read_secure_text
from scripts.wa_ir_object_storage_preflight_agent import _validate_object_storage_url


SCHEMA = "three-site-wa-ir-object-storage-bootstrap-v1"
WA_IR_HOST = "95.38.164.29"
WA_IR_USER = "ubuntu"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_BOOTSTRAP_ARTIFACT_BYTES = 4 * 1024 * 1024
REMOTE_AGENT = "/usr/local/sbin/wa-ir-object-storage-preflight-agent.py"
REMOTE_MANIFEST = "/run/wa-ir-object-storage-preflight/manifest.json"

# This fixed bootstrap receives only presigned Object Storage URLs and hashes.
# All file bytes are downloaded by WA-IR over HTTPS; SSH carries only this
# bounded command and the final JSON status.
REMOTE_BOOTSTRAP = r'''import hashlib,json,os,pathlib,subprocess,sys,urllib.request
def fetch(url, expected_hash, expected_size, target):
    path=pathlib.Path(target); path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.download'); temporary.unlink(missing_ok=True)
    request=urllib.request.Request(url,headers={'User-Agent':'trading-bot-wa-ir-bootstrap/1'},method='GET')
    digest=hashlib.sha256(); size=0
    try:
        with urllib.request.urlopen(request,timeout=120) as response, temporary.open('xb') as output:
            while True:
                chunk=response.read(65536)
                if not chunk: break
                size+=len(chunk)
                if size>4194304: raise RuntimeError('bootstrap artifact exceeds bound')
                digest.update(chunk); output.write(chunk)
            output.flush(); os.fsync(output.fileno())
        if size!=expected_size or digest.hexdigest()!=expected_hash: raise RuntimeError('bootstrap artifact hash/size mismatch')
        os.chmod(temporary,0o700 if target.endswith('.py') else 0o600); os.replace(temporary,path)
    except Exception:
        temporary.unlink(missing_ok=True); raise
fetch(sys.argv[1],sys.argv[2],int(sys.argv[3]),sys.argv[4])
fetch(sys.argv[5],sys.argv[6],int(sys.argv[7]),sys.argv[8])
result=subprocess.run(['/usr/bin/python3','-I','-B',sys.argv[4],'--manifest',sys.argv[8]],text=True,capture_output=True,timeout=3600)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
raise SystemExit(result.returncode)
'''


class BootstrapError(RuntimeError):
    pass


def load_descriptor(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > 128 * 1024
        ):
            raise BootstrapError("bootstrap descriptor must be one root-only regular file")
        payload = json.loads(read_secure_text(path, label="WA-IR bootstrap descriptor", max_size=128 * 1024))
    except Exception as exc:
        if isinstance(exc, BootstrapError):
            raise
        raise BootstrapError("bootstrap descriptor is unavailable or invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "release_sha", "expires_in_seconds", "agent", "manifest"
    }:
        raise BootstrapError("bootstrap descriptor fields are invalid")
    if payload["schema"] != SCHEMA or not re.fullmatch(r"[0-9a-f]{40}", str(payload["release_sha"])):
        raise BootstrapError("bootstrap descriptor identity is invalid")
    if (
        isinstance(payload["expires_in_seconds"], bool)
        or not isinstance(payload["expires_in_seconds"], int)
        or not 60 <= payload["expires_in_seconds"] <= 900
    ):
        raise BootstrapError("bootstrap descriptor lifetime is invalid")
    for label in ("agent", "manifest"):
        artifact = payload[label]
        if not isinstance(artifact, dict) or set(artifact) != {"url", "sha256", "bytes"}:
            raise BootstrapError(f"bootstrap {label} fields are invalid")
        _validate_object_storage_url(str(artifact["url"]), label=f"bootstrap {label}")
        if not SHA256_RE.fullmatch(str(artifact["sha256"])):
            raise BootstrapError(f"bootstrap {label} hash is invalid")
        if (
            isinstance(artifact["bytes"], bool)
            or not isinstance(artifact["bytes"], int)
            or artifact["bytes"] <= 0
            or artifact["bytes"] > MAX_BOOTSTRAP_ARTIFACT_BYTES
        ):
            raise BootstrapError(f"bootstrap {label} size is invalid")
    return payload


def remote_command(payload: dict[str, Any]) -> str:
    agent = payload["agent"]
    manifest = payload["manifest"]
    arguments = [
        "/usr/bin/python3", "-I", "-B", "-c", REMOTE_BOOTSTRAP,
        str(agent["url"]), str(agent["sha256"]), str(agent["bytes"]), REMOTE_AGENT,
        str(manifest["url"]), str(manifest["sha256"]), str(manifest["bytes"]), REMOTE_MANIFEST,
    ]
    command = " ".join(shlex.quote(value) for value in arguments)
    if len(command.encode()) > 65_536:
        raise BootstrapError("bootstrap SSH command exceeds its fixed control-plane bound")
    return command


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.host != WA_IR_HOST or args.port != 22 or args.user != WA_IR_USER:
        raise BootstrapError("WA-IR SSH target differs from the pinned replacement host")
    try:
        identity_metadata = args.identity.lstat()
    except OSError as exc:
        raise BootstrapError("WA-IR SSH identity is unavailable") from exc
    if (
        not stat.S_ISREG(identity_metadata.st_mode)
        or identity_metadata.st_uid != 0
        or identity_metadata.st_nlink != 1
        or stat.S_IMODE(identity_metadata.st_mode) != 0o600
    ):
        raise BootstrapError("WA-IR SSH identity must be one root-only regular file")
    payload = load_descriptor(args.descriptor)
    command_only = (
        "sudo -n -- /usr/bin/python3 -I -B -c "
        + shlex.quote(REMOTE_BOOTSTRAP)
        + " "
        + " ".join(
            shlex.quote(value)
            for value in (
                str(payload["agent"]["url"]),
                str(payload["agent"]["sha256"]),
                str(payload["agent"]["bytes"]),
                REMOTE_AGENT,
                str(payload["manifest"]["url"]),
                str(payload["manifest"]["sha256"]),
                str(payload["manifest"]["bytes"]),
                REMOTE_MANIFEST,
            )
        )
    )
    if len(command_only.encode()) > 65_536:
        raise BootstrapError("bootstrap SSH command exceeds its fixed control-plane bound")
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "LogLevel=ERROR",
        "-p", str(args.port), "-i", str(args.identity),
        f"{args.user}@{args.host}", command_only,
    ]
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=3700,
    )
    if completed.returncode != 0:
        raise BootstrapError("WA-IR Object Storage preflight command failed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BootstrapError("WA-IR Object Storage preflight returned non-JSON output") from exc
    if result.get("status") != "wa-ir-preflight-complete" or result.get("release_sha") != payload["release_sha"]:
        raise BootstrapError("WA-IR Object Storage preflight did not return the pinned success state")
    return {
        "status": "verified",
        "role": "webapp-ir",
        "release_sha": payload["release_sha"],
        "remote_preflight_status": result["status"],
        "ssh_usage": "bounded-command-and-status-only",
        "payload_transport": "private-arvan-object-storage",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--host", default=WA_IR_HOST)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default=WA_IR_USER)
    args = parser.parse_args(argv)
    try:
        result = execute(args)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
