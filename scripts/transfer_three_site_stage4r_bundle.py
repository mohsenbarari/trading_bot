#!/usr/bin/env python3
"""Deliver one encrypted Stage 4R role bundle through Arvan Object Storage.

The artifact is encrypted to a role-local age identity, uploaded to the
private/versioned staging bucket, read back at its exact VersionId, and then
downloaded by the target over HTTPS.  SSH is restricted to a bounded receiver
command plus a redacted JSON receipt; it carries no artifact bytes or secrets.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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


STAGING_BUCKET = "gold-trade-staging-three-site-dr"
ROLE_HOSTS = {
    "bot-fi": "130.185.121.98",
    "webapp-fi": "194.5.206.69",
    "webapp-ir": "188.213.198.115",
    "witness": "130.185.121.152",
}
PRODUCTION_IPS = frozenset({"65.109.216.187", "65.109.220.59", "95.38.164.29", "37.152.191.11"})
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RECIPIENT_RE = re.compile(r"^age1[0-9a-z]{40,80}$")
PREFIX_RE = re.compile(r"^staging/[a-z0-9][a-z0-9._/-]{2,240}/$")
REMOTE_IDENTITY = "/etc/trading-bot-three-site/stage4r/transport/age-identity.txt"
MAX_BUNDLE_BYTES = 512 * 1024 * 1024


REMOTE_RECEIVER = r'''import hashlib,json,os,pathlib,re,stat,subprocess,sys,urllib.parse,urllib.request
url,cipher_hash,cipher_size,plain_hash,plain_size,destination,identity=sys.argv[1:]
cipher_size=int(cipher_size); plain_size=int(plain_size); target=pathlib.Path(destination)
parsed=urllib.parse.urlsplit(url)
host=(parsed.hostname or '').lower().rstrip('.')
if parsed.scheme!='https' or not (host=='s3.ir-thr-at1.arvanstorage.ir' or host.endswith('.s3.ir-thr-at1.arvanstorage.ir')) or parsed.username or parsed.password or parsed.fragment: raise RuntimeError('bundle URL is outside approved Object Storage')
if not re.fullmatch(r'/var/tmp/three-site-stage4r/(bot-fi|webapp-fi|webapp-ir|witness)/[0-9a-f]{40}/stage4r-(bot-fi|webapp-fi|webapp-ir|witness)-[0-9a-f]{8}\.tar',str(target)): raise RuntimeError('bundle destination is outside Stage 4R root')
if identity!='/etc/trading-bot-three-site/stage4r/transport/age-identity.txt': raise RuntimeError('age identity path drifted')
meta=pathlib.Path(identity).lstat()
if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or stat.S_IMODE(meta.st_mode)!=0o600 or meta.st_nlink!=1: raise RuntimeError('age identity is unsafe')
target.parent.mkdir(mode=0o700,parents=True,exist_ok=True); os.chmod(target.parent,0o700)
def digest(path,limit):
 h=hashlib.sha256(); size=0
 with path.open('rb') as source:
  while True:
   chunk=source.read(1048576)
   if not chunk: break
   size+=len(chunk)
   if size>limit: raise RuntimeError('bundle exceeds bound')
   h.update(chunk)
 return h.hexdigest(),size
if target.exists():
 actual_hash,actual_size=digest(target,plain_size)
 if actual_hash!=plain_hash or actual_size!=plain_size: raise RuntimeError('existing bundle identity differs')
 print(json.dumps({'status':'already-installed','sha256':actual_hash,'bytes':actual_size,'destination_name':target.name},sort_keys=True)); raise SystemExit(0)
encrypted=target.with_name('.'+target.name+'.age-next'); plain=target.with_name('.'+target.name+'.plain-next')
encrypted.unlink(missing_ok=True); plain.unlink(missing_ok=True)
try:
 request=urllib.request.Request(url,headers={'User-Agent':'trading-bot-stage4r-bundle/1'},method='GET')
 h=hashlib.sha256(); size=0
 with urllib.request.urlopen(request,timeout=300) as response,encrypted.open('xb') as output:
  while True:
   chunk=response.read(1048576)
   if not chunk: break
   size+=len(chunk)
   if size>cipher_size: raise RuntimeError('ciphertext exceeds expected size')
   h.update(chunk); output.write(chunk)
  output.flush(); os.fsync(output.fileno())
 if size!=cipher_size or h.hexdigest()!=cipher_hash: raise RuntimeError('ciphertext identity mismatch')
 result=subprocess.run(['/usr/bin/age','--decrypt','--identity',identity,'--output',str(plain),str(encrypted)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=1800,env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'})
 if result.returncode!=0: raise RuntimeError('bundle decryption failed closed')
 actual_hash,actual_size=digest(plain,plain_size)
 if actual_hash!=plain_hash or actual_size!=plain_size: raise RuntimeError('plaintext identity mismatch')
 os.chmod(plain,0o600); os.replace(plain,target)
 descriptor=os.open(target.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)); os.fsync(descriptor); os.close(descriptor)
 print(json.dumps({'status':'installed','sha256':actual_hash,'bytes':actual_size,'destination_name':target.name},sort_keys=True))
finally:
 encrypted.unlink(missing_ok=True); plain.unlink(missing_ok=True)
'''


class Stage4RBundleTransferError(RuntimeError):
    """A Stage 4R artifact cannot be transferred safely."""


def destination_for(*, role: str, release_sha: str) -> Path:
    if role not in ROLE_HOSTS or RELEASE_RE.fullmatch(release_sha) is None:
        raise Stage4RBundleTransferError("role or release identity is invalid")
    return Path(
        f"/var/tmp/three-site-stage4r/{role}/{release_sha}/"
        f"stage4r-{role}-{release_sha[:8]}.tar"
    )


def confirmation_phrase(*, role: str, release_sha: str, digest: str) -> str:
    return f"transfer-stage4r-bundle:{role}:{release_sha}:{digest}"


def _source_identity(source: Path, *, role: str, release_sha: str) -> tuple[str, int]:
    expected_name = f"stage4r-{role}-{release_sha[:8]}.tar"
    if source.name != expected_name:
        raise Stage4RBundleTransferError("bundle filename is not bound to role/release")
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise Stage4RBundleTransferError("bundle source is unavailable") from exc
    if (
        source.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise Stage4RBundleTransferError("bundle source is not one owner-only regular file")
    return _hash_regular(source, label="Stage 4R role bundle", max_size=MAX_BUNDLE_BYTES)


def _remote_receive(
    *,
    host: str,
    ssh_identity: Path,
    known_hosts: Path,
    proxy_host: str | None,
    proxy_known_hosts: Path | None,
    url: str,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
    plaintext_sha256: str,
    plaintext_bytes: int,
    destination: Path,
) -> dict[str, Any]:
    command = [
        "/usr/bin/python3", "-I", "-B", "-c", REMOTE_RECEIVER,
        url, ciphertext_sha256, str(ciphertext_bytes), plaintext_sha256,
        str(plaintext_bytes), str(destination), REMOTE_IDENTITY,
    ]
    arguments = [
        "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}", "-i", str(ssh_identity),
    ]
    if proxy_host is not None:
        if proxy_host != "185.231.182.6" or proxy_known_hosts is None:
            raise Stage4RBundleTransferError("WebApp-IR proxy configuration is invalid")
        proxy = (
            "/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=10 "
            "-o StrictHostKeyChecking=yes "
            f"-o UserKnownHostsFile={shlex.quote(str(proxy_known_hosts))} "
            f"-W %h:%p root@{proxy_host}"
        )
        arguments.extend(["-o", f"ProxyCommand={proxy}"])
    arguments.extend([f"root@{host}", " ".join(shlex.quote(value) for value in command)])
    completed = subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        check=False,
        timeout=3600,
    )
    if completed.returncode != 0:
        diagnostic = str(completed.stderr).lower()
        safe_codes = {
            "bundle url is outside approved object storage": "remote_url_policy",
            "bundle destination is outside stage 4r root": "remote_destination_policy",
            "age identity path drifted": "remote_identity_path",
            "age identity is unsafe": "remote_identity_safety",
            "ciphertext exceeds expected size": "remote_ciphertext_size",
            "ciphertext identity mismatch": "remote_ciphertext_identity",
            "bundle decryption failed closed": "remote_decryption",
            "plaintext identity mismatch": "remote_plaintext_identity",
            "existing bundle identity differs": "remote_existing_identity",
            "host key verification failed": "ssh_host_key",
            "permission denied": "ssh_authentication",
        }
        code = next(
            (value for marker, value in safe_codes.items() if marker in diagnostic),
            "remote_receiver",
        )
        raise Stage4RBundleTransferError(f"remote receiver failed closed: {code}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise Stage4RBundleTransferError("remote bundle receipt is not JSON") from exc
    if (
        result.get("status") not in {"installed", "already-installed"}
        or result.get("sha256") != plaintext_sha256
        or result.get("bytes") != plaintext_bytes
        or result.get("destination_name") != destination.name
    ):
        raise Stage4RBundleTransferError("remote bundle receipt differs from the published artifact")
    return result


def execute(args: argparse.Namespace, *, client=None) -> dict[str, Any]:  # noqa: ANN001
    role = str(args.role)
    release_sha = str(args.release_sha).lower()
    if role not in ROLE_HOSTS or RELEASE_RE.fullmatch(release_sha) is None:
        raise Stage4RBundleTransferError("role or release SHA is invalid")
    host = ROLE_HOSTS[role]
    if host in PRODUCTION_IPS or args.host != host:
        raise Stage4RBundleTransferError("target host differs from the fixed staging role")
    recipient = str(args.recipient).strip()
    if RECIPIENT_RE.fullmatch(recipient) is None:
        raise Stage4RBundleTransferError("role age recipient is malformed")
    prefix = str(args.prefix).strip("/") + "/"
    if PREFIX_RE.fullmatch(prefix) is None or ".." in Path(prefix).parts:
        raise Stage4RBundleTransferError("Object Storage prefix is invalid")
    if args.bucket != STAGING_BUCKET:
        raise Stage4RBundleTransferError("only the dedicated private staging bucket is allowed")
    if (role == "webapp-ir") != (args.proxy_host is not None):
        raise Stage4RBundleTransferError("only WebApp-IR may use the fixed relay")
    digest, size = _source_identity(args.source, role=role, release_sha=release_sha)
    destination = destination_for(role=role, release_sha=release_sha)
    expected = confirmation_phrase(role=role, release_sha=release_sha, digest=digest)
    if not args.apply:
        return {
            "status": "planned",
            "role": role,
            "host": host,
            "release_sha": release_sha,
            "bundle_sha256": digest,
            "bundle_bytes": size,
            "destination": str(destination),
            "required_confirmation": expected,
            "payload_transport": "private-versioned-object-storage-cse",
            "ssh_payload_transfer": False,
        }
    if args.confirm != expected:
        raise Stage4RBundleTransferError("bundle transfer confirmation mismatch")
    if args.evidence.exists() or args.evidence.is_symlink():
        raise Stage4RBundleTransferError("bundle transfer evidence already exists")
    if client is None:
        client = _client(_credentials(args.credentials))
    require_private_versioned_bucket(client, bucket=args.bucket)
    with tempfile.TemporaryDirectory(prefix="stage4r-bundle-") as temporary:
        encrypted = Path(temporary) / (args.source.name + ".age")
        cipher_hash, cipher_size = encrypt(args.source, encrypted, recipient)
        object_key = f"{prefix}{release_sha}/{role}/{digest}/{args.source.name}.age"
        obj = _upload_and_readback(
            client,
            bucket=args.bucket,
            key=object_key,
            source=encrypted,
            metadata={
                "kind": "stage4r-role-bundle",
                "role": role,
                "release-sha": release_sha,
                "plaintext-sha256": digest,
            },
        )
        remote = _remote_receive(
            host=host,
            ssh_identity=args.ssh_identity,
            known_hosts=args.known_hosts,
            proxy_host=args.proxy_host,
            proxy_known_hosts=args.proxy_known_hosts,
            url=_presigned_get(client, bucket=args.bucket, obj=obj, ttl=900),
            ciphertext_sha256=cipher_hash,
            ciphertext_bytes=cipher_size,
            plaintext_sha256=digest,
            plaintext_bytes=size,
            destination=destination,
        )
    evidence = {
        "schema": "three-site-stage4r-object-storage-bundle-transfer-v1",
        "status": "encrypted-upload-readback-and-remote-install-verified",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "host": host,
        "release_sha": release_sha,
        "bucket": args.bucket,
        "object_key": obj["object_key"],
        "version_id": obj["version_id"],
        "plaintext_sha256": digest,
        "plaintext_bytes": size,
        "ciphertext_sha256": cipher_hash,
        "ciphertext_bytes": cipher_size,
        "recipient_sha256": hashlib.sha256((recipient + "\n").encode()).hexdigest(),
        "destination": str(destination),
        "remote_result": remote,
        "presigned_url_persisted": False,
        "ssh_payload_transfer": False,
        "production_touched": False,
    }
    write_secure_new_bytes(
        args.evidence,
        (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode(),
        label="Stage 4R Object Storage bundle transfer evidence",
        mode=0o600,
    )
    return {
        "status": evidence["status"],
        "role": role,
        "release_sha": release_sha,
        "version_id": obj["version_id"],
        "evidence": str(args.evidence),
        "ssh_payload_transfer": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(ROLE_HOSTS), required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--bucket", default=STAGING_BUCKET)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--ssh-identity", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--proxy-host")
    parser.add_argument("--proxy-known-hosts", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        result = execute(parse_args(argv))
    except Exception as exc:
        # All Stage4RBundleTransferError messages are locally generated fixed
        # diagnostics; never surface remote stderr, presigned URLs, or secrets.
        payload = {"status": "blocked", "error_class": type(exc).__name__}
        if isinstance(exc, Stage4RBundleTransferError):
            payload["error"] = str(exc)
        print(json.dumps(payload, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
