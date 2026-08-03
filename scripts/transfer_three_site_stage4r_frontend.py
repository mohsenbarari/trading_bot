#!/usr/bin/env python3
"""Transfer the untracked staging frontend through private Object Storage.

The frontend build is intentionally outside the Git bundle.  This helper
keeps that artifact on the same CSE + private/versioned Object Storage path as
the role bundles; SSH carries only a bounded receiver command and its JSON
receipt.  The receiver uses bounded HTTP Range requests so a long stream stall
cannot leave an accepted partial artifact.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import stat
import subprocess
import sys
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


BUCKET = "gold-trade-staging-three-site-dr"
ROLE_HOSTS = {
    "bot-fi": "130.185.121.98",
    "webapp-fi": "194.5.206.69",
    "webapp-ir": "188.213.198.115",
    "witness": "130.185.121.152",
}
PRODUCTION_IPS = frozenset(
    {"65.109.216.187", "65.109.220.59", "95.38.164.29", "37.152.191.11"}
)
IDENTITY = "/etc/trading-bot-three-site/stage4r/transport/age-identity.txt"
RECEIVER = r'''import hashlib,json,os,pathlib,stat,subprocess,sys,tarfile,time,urllib.parse,urllib.request
url,cipher_hash,cipher_size,plain_hash,plain_size,release,identity=sys.argv[1:]
cipher_size=int(cipher_size); plain_size=int(plain_size)
parsed=urllib.parse.urlsplit(url); host=(parsed.hostname or '').lower().rstrip('.')
if parsed.scheme!='https' or not (host=='s3.ir-thr-at1.arvanstorage.ir' or host.endswith('.s3.ir-thr-at1.arvanstorage.ir')) or parsed.username or parsed.password or parsed.fragment: raise RuntimeError('frontend URL is outside approved Object Storage')
if identity!='/etc/trading-bot-three-site/stage4r/transport/age-identity.txt': raise RuntimeError('age identity path drifted')
if not release.isalnum() or len(release)!=40: raise RuntimeError('release identity is invalid')
meta=pathlib.Path(identity).lstat()
if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or stat.S_IMODE(meta.st_mode)!=0o600 or meta.st_nlink!=1: raise RuntimeError('age identity is unsafe')
source=pathlib.Path('/srv/trading-bot-three-site-staging-data/releases')/release/'source'
target=source/'mini_app_dist_staging'
if not source.is_dir() or target.exists(): raise RuntimeError('frontend target is not a fresh release source root')
work=source/'.mini_app_dist_staging.next'; encrypted=work.with_name('.mini_app_dist_staging.tar.age-next'); plain=work.with_name('.mini_app_dist_staging.tar.plain-next')
work.unlink(missing_ok=True); encrypted.unlink(missing_ok=True); plain.unlink(missing_ok=True)
def digest(path,limit):
 h=hashlib.sha256(); size=0
 with path.open('rb') as f:
  while True:
   chunk=f.read(1048576)
   if not chunk: break
   size+=len(chunk)
   if size>limit: raise RuntimeError('frontend archive exceeds bound')
   h.update(chunk)
 return h.hexdigest(),size
try:
 h=hashlib.sha256(); total=0; part=4*1024*1024
 with encrypted.open('xb') as out:
  for start in range(0,cipher_size,part):
   end=min(cipher_size-1,start+part-1); expected=end-start+1
   for attempt in range(4):
    try:
     request=urllib.request.Request(url,headers={'User-Agent':'trading-bot-stage4r-frontend-range/1','Range':f'bytes={start}-{end}'},method='GET')
     with urllib.request.urlopen(request,timeout=90) as response:
      body=response.read(expected)
      if len(body)!=expected: raise RuntimeError('frontend range length')
     out.write(body); h.update(body); total+=len(body); break
    except Exception:
     if attempt==3: raise
     time.sleep(2**attempt)
  out.flush(); os.fsync(out.fileno())
 if total!=cipher_size or h.hexdigest()!=cipher_hash: raise RuntimeError('frontend ciphertext identity mismatch')
 result=subprocess.run(['/usr/bin/age','--decrypt','--identity',identity,'--output',str(plain),str(encrypted)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=900,env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','LANG':'C.UTF-8','LC_ALL':'C'})
 if result.returncode!=0: raise RuntimeError('frontend decryption failed closed')
 actual_hash,actual_size=digest(plain,plain_size)
 if actual_hash!=plain_hash or actual_size!=plain_size: raise RuntimeError('frontend plaintext identity mismatch')
 work.mkdir(mode=0o700)
 with tarfile.open(plain,'r') as archive:
  members=archive.getmembers()
  if not members or any((m.name!='mini_app_dist_staging' and not m.name.startswith('mini_app_dist_staging/')) or m.issym() or m.islnk() or not (m.isdir() or m.isfile()) for m in members): raise RuntimeError('frontend archive member policy failed')
  for member in members:
   resolved=(work/member.name).resolve()
   if not resolved.is_relative_to(work.resolve()): raise RuntimeError('frontend archive path escape')
   archive.extract(member,work)
 os.replace(work/'mini_app_dist_staging',target)
 descriptor=os.open(source,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)); os.fsync(descriptor); os.close(descriptor)
 print(json.dumps({'status':'installed','sha256':actual_hash,'bytes':actual_size,'destination':str(target)},sort_keys=True))
finally:
 encrypted.unlink(missing_ok=True); plain.unlink(missing_ok=True)
 if work.exists():
  import shutil
  shutil.rmtree(work,ignore_errors=True)
'''


class TransferError(RuntimeError):
    pass


def _shell_quote(value: str) -> str:
    return shlex.quote(value)


def _receive(*, host: str, identity: Path, known_hosts: Path, proxy_host: str | None,
             proxy_known_hosts: Path | None, url: str, cipher_hash: str,
             cipher_size: int, plain_hash: str, plain_size: int, release: str) -> dict[str, Any]:
    values = [url, cipher_hash, str(cipher_size), plain_hash, str(plain_size), release, IDENTITY]
    remote = " ".join(_shell_quote(v) for v in ["/usr/bin/python3", "-I", "-B", "-c", RECEIVER, *values])
    args = ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}", "-i", str(identity)]
    if proxy_host:
        if proxy_host != "185.231.182.6" or proxy_known_hosts is None:
            raise TransferError("WebApp-IR proxy configuration is invalid")
        proxy = ("/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes "
                 f"-o UserKnownHostsFile={shlex.quote(str(proxy_known_hosts))} -W %h:%p root@{proxy_host}")
        args += ["-o", f"ProxyCommand={proxy}"]
    args += [f"root@{host}", remote]
    completed = subprocess.run(args, text=True, capture_output=True, check=False, timeout=3600)
    if completed.returncode:
        raise TransferError("frontend receiver failed closed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TransferError("frontend receipt is not JSON") from exc
    if result.get("status") != "installed" or result.get("sha256") != plain_hash or result.get("bytes") != plain_size:
        raise TransferError("frontend receipt differs from published artifact")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=sorted(ROLE_HOSTS), required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--ssh-identity", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--proxy-host")
    parser.add_argument("--proxy-known-hosts", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--confirm")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    role = str(args.role); release = str(args.release_sha).lower()
    if args.host != ROLE_HOSTS[role] or args.host in PRODUCTION_IPS or len(release) != 40:
        raise TransferError("fixed staging identity check failed")
    digest, size = _hash_regular(args.source, label="staging frontend archive", max_size=128 * 1024 * 1024)
    expected = f"transfer-stage4r-frontend:{role}:{release}:{digest}"
    if not args.apply:
        print(json.dumps({"status":"planned","required_confirmation":expected,"sha256":digest,"bytes":size,"ssh_payload_transfer":False},sort_keys=True)); return 0
    if args.confirm != expected:
        raise TransferError("frontend transfer confirmation mismatch")
    client = _client(_credentials(args.credentials)); require_private_versioned_bucket(client,bucket=BUCKET)
    with __import__('tempfile').TemporaryDirectory(prefix='stage4r-frontend-') as temporary:
        encrypted=Path(temporary)/'mini_app_dist_staging.tar.age'
        cipher_hash,cipher_size=encrypt(args.source,encrypted,args.recipient)
        key=f"{args.prefix.strip('/')}/{release}/{role}/{digest}/mini_app_dist_staging.tar.age"
        obj=_upload_and_readback(client,bucket=BUCKET,key=key,source=encrypted,metadata={"kind":"stage4r-frontend","release-sha":release,"role":role,"plaintext-sha256":digest})
        receipt=_receive(host=args.host,identity=args.ssh_identity,known_hosts=args.known_hosts,proxy_host=args.proxy_host,proxy_known_hosts=args.proxy_known_hosts,url=_presigned_get(client,bucket=BUCKET,obj=obj,ttl=900),cipher_hash=cipher_hash,cipher_size=cipher_size,plain_hash=digest,plain_size=size,release=release)
    evidence={"schema":"three-site-stage4r-frontend-transfer-v1","status":"encrypted-upload-readback-and-remote-install-verified","created_at":datetime.now(timezone.utc).isoformat(),"role":role,"host":args.host,"release_sha":release,"bucket":BUCKET,"object_key":obj["object_key"],"version_id":obj["version_id"],"plaintext_sha256":digest,"plaintext_bytes":size,"ciphertext_sha256":cipher_hash,"ciphertext_bytes":cipher_size,"destination":receipt["destination"],"ssh_payload_transfer":False,"production_touched":False}
    write_secure_new_bytes(args.evidence,(json.dumps(evidence,sort_keys=True,indent=2)+'\n').encode(),label='Stage 4R frontend transfer evidence',mode=0o600)
    print(json.dumps({"status":evidence["status"],"role":role,"version_id":obj["version_id"],"evidence":str(args.evidence),"ssh_payload_transfer":False},sort_keys=True)); return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status":"blocked","error_class":type(exc).__name__,"error":str(exc)},sort_keys=True))
        raise SystemExit(1)
