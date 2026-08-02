#!/usr/bin/env python3
"""Transfer one approved Stage 3 artifact to disposable WebApp-IR.

Payload bytes travel through the private, versioned staging bucket encrypted
with the target host's age recipient.  SSH carries only a short-lived
presigned URL, immutable hashes, and the final redacted result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_new_bytes
from scripts.publish_wa_ir_object_storage_preflight import (
    _client,
    _credentials,
    _hash_regular,
    _presigned_get,
    _upload_and_readback,
    encrypt,
    require_private_versioned_bucket,
)
from scripts.verify_three_site_staging_inventory import (
    load_inventory,
    verify_approved_inventory,
)


PRODUCTION_IPS = frozenset(
    {"65.109.216.187", "65.109.220.59", "95.38.164.29", "37.152.191.11"}
)
ARVAN_DATA_HOST = "s3.ir-thr-at1.arvanstorage.ir"
RELAY_IP = "185.231.182.6"
REMOTE_AGE_IDENTITY = Path(
    "/root/secure-envs/trading-bot/stage3-fd34231d-age-identity.txt"
)
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
AGE_RECIPIENT_RE = re.compile(r"^age1[0-9a-z]{40,80}$")


REMOTE_RECEIVER = r'''import hashlib,json,os,pathlib,re,stat,subprocess,sys,urllib.parse,urllib.request
url,cipher_hash,cipher_size,plain_hash,plain_size,destination,identity=sys.argv[1:]
cipher_size=int(cipher_size); plain_size=int(plain_size); target=pathlib.Path(destination)
parsed=urllib.parse.urlsplit(url)
if parsed.scheme!='https' or parsed.hostname!='s3.ir-thr-at1.arvanstorage.ir' or parsed.username or parsed.password or parsed.fragment: raise RuntimeError('artifact URL is outside approved Object Storage')
if not re.fullmatch(r'/tmp/stage3-[0-9a-f]{8}-[0-9a-f]{8}-transfer/(three-site-stage3-[0-9a-f]{8}\.bundle|trading-bot-postgres-boottime-[0-9a-f]{8}\.tar\.zst|trading-bot-three-site-app-[0-9a-f]{8}\.tar\.zst|trading-bot-three-site-third-party-[0-9a-f]{8}\.tar\.zst)',destination): raise RuntimeError('artifact destination is outside approved transfer root')
if identity!='/root/secure-envs/trading-bot/stage3-fd34231d-age-identity.txt': raise RuntimeError('artifact identity path drifted')
meta=pathlib.Path(identity).lstat()
if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or stat.S_IMODE(meta.st_mode)!=0o600 or meta.st_nlink!=1: raise RuntimeError('artifact age identity is unsafe')
target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
parent=target.parent.lstat()
if not stat.S_ISDIR(parent.st_mode) or parent.st_uid not in {0,1000} or stat.S_IMODE(parent.st_mode)&0o077: raise RuntimeError('artifact transfer directory is unsafe')
encrypted=target.with_name('.'+target.name+'.age-next'); plain=target.with_name('.'+target.name+'.plain-next')
encrypted.unlink(missing_ok=True); plain.unlink(missing_ok=True)
def digest(path,limit):
 h=hashlib.sha256(); size=0
 with path.open('rb') as source:
  while True:
   chunk=source.read(1048576)
   if not chunk: break
   size+=len(chunk)
   if size>limit: raise RuntimeError('artifact exceeds bound')
   h.update(chunk)
 return h.hexdigest(),size
try:
 request=urllib.request.Request(url,headers={'User-Agent':'trading-bot-stage3-artifact/1'},method='GET')
 h=hashlib.sha256(); size=0
 with urllib.request.urlopen(request,timeout=300) as response,encrypted.open('xb') as output:
  while True:
   chunk=response.read(1048576)
   if not chunk: break
   size+=len(chunk)
   if size>cipher_size: raise RuntimeError('artifact ciphertext exceeds expected size')
   h.update(chunk); output.write(chunk)
  output.flush(); os.fsync(output.fileno())
 if size!=cipher_size or h.hexdigest()!=cipher_hash: raise RuntimeError('artifact ciphertext identity mismatch')
 result=subprocess.run(['/usr/bin/age','--decrypt','--identity',identity,'--output',str(plain),str(encrypted)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=1800,env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'})
 if result.returncode!=0: raise RuntimeError('artifact decryption failed closed')
 actual_hash,actual_size=digest(plain,plain_size)
 if actual_hash!=plain_hash or actual_size!=plain_size: raise RuntimeError('artifact plaintext identity mismatch')
 os.chmod(plain,0o600); os.replace(plain,target)
 descriptor=os.open(target.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)); os.fsync(descriptor); os.close(descriptor)
 print(json.dumps({'status':'installed','sha256':actual_hash,'bytes':actual_size,'destination_name':target.name},sort_keys=True))
finally:
 encrypted.unlink(missing_ok=True); plain.unlink(missing_ok=True)
'''


class Stage3ArtifactTransferError(RuntimeError):
    pass


def artifact_spec(source: Path, *, release_sha: str, campaign_id: str) -> tuple[str, Path]:
    release_short = release_sha[:8]
    allowed = {
        f"three-site-stage3-{release_short}.bundle",
        f"trading-bot-postgres-boottime-{release_short}.tar.zst",
        f"trading-bot-three-site-app-{release_short}.tar.zst",
        f"trading-bot-three-site-third-party-{release_short}.tar.zst",
    }
    if source.name not in allowed:
        raise Stage3ArtifactTransferError("Stage 3 artifact filename is outside the allowlist")
    campaign_short = campaign_id.replace("-", "")[:8]
    destination = Path(
        f"/tmp/stage3-{release_short}-{campaign_short}-transfer/{source.name}"
    )
    return source.name, destination


def _source_identity(source: Path) -> tuple[str, int]:
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise Stage3ArtifactTransferError("Stage 3 artifact source is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_size <= 0
        or metadata.st_size > MAX_ARTIFACT_BYTES
    ):
        raise Stage3ArtifactTransferError("Stage 3 artifact source is unsafe")
    return _hash_regular(
        source,
        label="Stage 3 artifact",
        max_size=MAX_ARTIFACT_BYTES,
    )


def confirmation_phrase(campaign_id: str, role: str, digest: str) -> str:
    return f"transfer-stage3-artifact:{campaign_id}:{role}:{digest}"


def require_transfer_inventory_stage(approved: dict[str, Any]) -> str:
    stage = str(approved.get("inventory_stage", ""))
    if stage not in {"planned", "provisioned"}:
        raise Stage3ArtifactTransferError(
            "artifact transfer requires an approved planned or provisioned inventory"
        )
    return stage


def _ssh_receive(
    *,
    host: str,
    identity: Path,
    known_hosts: Path,
    url: str,
    cipher_hash: str,
    cipher_size: int,
    plain_hash: str,
    plain_size: int,
    destination: Path,
) -> dict[str, Any]:
    arguments = [
        "/usr/bin/python3", "-I", "-B", "-c", REMOTE_RECEIVER,
        url, cipher_hash, str(cipher_size), plain_hash, str(plain_size),
        str(destination), str(REMOTE_AGE_IDENTITY),
    ]
    remote = "sudo -n -- " + " ".join(shlex.quote(value) for value in arguments)
    proxy = (
        "ssh -i /root/.ssh/id_rsa -o IdentitiesOnly=yes -o BatchMode=yes "
        "-o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts "
        f"-W %h:%p root@{RELAY_IP}"
    )
    completed = subprocess.run(
        [
            "ssh", "-i", str(identity), "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "HostKeyAlgorithms=ssh-ed25519",
            "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", f"ProxyCommand={proxy}", f"ubuntu@{host}", remote,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=3600,
    )
    if completed.returncode != 0:
        raise Stage3ArtifactTransferError("remote Stage 3 artifact receiver failed closed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise Stage3ArtifactTransferError("remote Stage 3 artifact result is not JSON") from exc
    if (
        result.get("status") != "installed"
        or result.get("sha256") != plain_hash
        or result.get("bytes") != plain_size
        or result.get("destination_name") != destination.name
    ):
        raise Stage3ArtifactTransferError("remote Stage 3 artifact result differs")
    return result


def execute(args: argparse.Namespace) -> dict[str, Any]:
    inventory = load_inventory(args.inventory)
    approved = verify_approved_inventory(
        inventory,
        approval=load_inventory(args.approval),
        approval_policy=load_inventory(args.approval_policy),
        host_destructive=True,
    )
    require_transfer_inventory_stage(approved)
    role = next(item for item in inventory["roles"] if item["role"] == "webapp_ir")
    host = str(role["host_ip"])
    if host in PRODUCTION_IPS or host != args.host:
        raise Stage3ArtifactTransferError("artifact target is production-owned or differs from inventory")
    name, destination = artifact_spec(
        args.source,
        release_sha=inventory["release_sha"],
        campaign_id=inventory["campaign_id"],
    )
    digest, size = _source_identity(args.source)
    recipient = str(args.recipient).strip()
    if AGE_RECIPIENT_RE.fullmatch(recipient) is None:
        raise Stage3ArtifactTransferError("target age recipient is malformed")
    expected = confirmation_phrase(inventory["campaign_id"], "webapp-ir", digest)
    if not args.apply:
        return {
            "status": "planned",
            "campaign_id": inventory["campaign_id"],
            "role": "webapp-ir",
            "host": host,
            "source_name": name,
            "sha256": digest,
            "bytes": size,
            "destination": str(destination),
            "required_confirmation": expected,
            "production_overlap": False,
        }
    if args.confirm != expected:
        raise Stage3ArtifactTransferError("artifact transfer confirmation mismatch")
    if args.output.exists() or args.output.is_symlink():
        raise Stage3ArtifactTransferError("artifact transfer evidence already exists")

    credentials = _credentials(args.credentials)
    client = _client(credentials)
    bucket = inventory["object_storage"]["bucket"]
    require_private_versioned_bucket(client, bucket=bucket)
    object_key = (
        f"{inventory['object_storage']['prefix']}transport/webapp-ir/"
        f"{digest}/{name}.age"
    )
    with tempfile.TemporaryDirectory(prefix="stage3-ir-artifact-") as raw:
        encrypted = Path(raw) / f"{name}.age"
        cipher_hash, cipher_size = encrypt(args.source, encrypted, recipient)
        obj = _upload_and_readback(
            client,
            bucket=bucket,
            key=object_key,
            source=encrypted,
            metadata={
                "kind": "stage3-artifact",
                "campaign-id": inventory["campaign_id"],
                "release-sha": inventory["release_sha"],
                "plaintext-sha256": digest,
            },
        )
        url = _presigned_get(client, bucket=bucket, obj=obj, ttl=900)
        remote = _ssh_receive(
            host=host,
            identity=args.ssh_identity,
            known_hosts=args.known_hosts,
            url=url,
            cipher_hash=cipher_hash,
            cipher_size=cipher_size,
            plain_hash=digest,
            plain_size=size,
            destination=destination,
        )
    evidence = {
        "schema": "three-site-stage3-ir-artifact-transfer-v1",
        "status": "encrypted-upload-readback-and-remote-install-verified",
        "campaign_id": inventory["campaign_id"],
        "release_sha": inventory["release_sha"],
        "role": "webapp-ir",
        "host": host,
        "source_name": name,
        "destination": str(destination),
        "plaintext_sha256": digest,
        "plaintext_bytes": size,
        "ciphertext_sha256": cipher_hash,
        "ciphertext_bytes": cipher_size,
        "bucket": bucket,
        "object_key": object_key,
        "version_id": obj["version_id"],
        "age_recipient_sha256": hashlib.sha256((recipient + "\n").encode()).hexdigest(),
        "remote_result": remote,
        "presigned_url_persisted": False,
        "relay_payload_cache_used": False,
        "production_touched": False,
    }
    write_secure_new_bytes(
        args.output,
        (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode(),
        label="Stage 3 IR artifact transfer evidence",
    )
    return {
        "status": evidence["status"],
        "source_name": name,
        "sha256": digest,
        "bytes": size,
        "evidence": str(args.output),
        "production_touched": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--ssh-identity", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        result = execute(args)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
