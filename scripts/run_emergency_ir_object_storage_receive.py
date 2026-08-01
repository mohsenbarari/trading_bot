#!/usr/bin/env python3
"""Bootstrap a sealed Emergency WA-IR receive without transferring payloads over SSH.

The controller reads a root-only descriptor containing only short-lived
Object-Storage URLs and hashes.  WA-IR then downloads the receiver bundle,
sealed manifest and URL map directly from private Arvan Object Storage.  The
receiver bundle carries the pinned manifest public key, validates the manifest,
and pulls the four encrypted campaign artifacts directly from Object Storage.
SSH carries only a bounded command and the final non-secret status JSON.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_text
from scripts.wa_ir_object_storage_preflight_agent import _validate_object_storage_url


SCHEMA = "gold-trade-emergency-ir-object-storage-bootstrap-v1"
WA_IR_HOST = "95.38.164.29"
WA_IR_USER = "ubuntu"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
MAX_DESCRIPTOR_BYTES = 128 * 1024
MAX_RECEIVER_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_SEALED_MANIFEST_BYTES = 128 * 1024
MAX_URL_MAP_BYTES = 64 * 1024
REMOTE_ROOT = "/run/trading-bot-emergency-bootstrap"


class EmergencyBootstrapError(RuntimeError):
    pass


# This small program is passed as a command, never as a release payload.  It
# downloads all files itself over HTTPS and accepts only a fixed, tiny archive
# layout.  The archive's hash arrives in the root-only descriptor and is
# checked before its pinned public key is used to verify the sealed manifest.
REMOTE_BOOTSTRAP = r'''import hashlib,json,os,pathlib,ssl,stat,subprocess,sys,tarfile,urllib.error,urllib.request
class BootstrapError(RuntimeError): pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): raise BootstrapError("unexpected redirect")
def fail(message): raise BootstrapError(message)
def secure_dir(path):
 path=pathlib.Path(path)
 if not path.is_absolute(): fail("remote root is invalid")
 current=pathlib.Path("/")
 for part in path.parts[1:]:
  current/=part
  try: state=current.lstat()
  except FileNotFoundError:
   current.mkdir(mode=0o700); state=current.lstat()
  if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode) or state.st_uid != 0 or stat.S_IMODE(state.st_mode)&0o022: fail("remote root is unsafe")
def fetch(url,digest,size,target):
 target=pathlib.Path(target); secure_dir(target.parent)
 if target.exists(): fail("refusing to overwrite bootstrap artifact")
 temporary=target.with_name("."+target.name+".download")
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
 observed=hashlib.sha256(); total=0
 try:
  request=urllib.request.Request(url,headers={"User-Agent":"gold-trade-emergency-ir-bootstrap/1"},method="GET")
  with opener.open(request,timeout=120) as response, temporary.open("xb") as output:
   if getattr(response,"status",200)!=200 or response.geturl()!=url: fail("bootstrap response differs from request")
   while True:
    chunk=response.read(65536)
    if not chunk: break
    total+=len(chunk)
    if total>size: fail("bootstrap artifact exceeds its sealed size")
    observed.update(chunk); output.write(chunk)
   output.flush(); os.fsync(output.fileno())
  if total!=size or observed.hexdigest()!=digest: fail("bootstrap artifact hash/size mismatch")
  os.chmod(temporary,0o600); os.replace(temporary,target)
 except Exception:
  try: temporary.unlink()
  except FileNotFoundError: pass
  raise
def extract_bundle(bundle,target):
 allowed={"run_receiver.py","signing-public.key","scripts/emergency_ir_object_storage_manifest.py","scripts/emergency_ir_object_storage_receiver.py"}
 secure_dir(target)
 with tarfile.open(bundle,"r:gz") as archive:
  members=archive.getmembers()
  if {member.name for member in members}!=allowed or len(members)!=len(allowed): fail("bootstrap bundle layout is invalid")
  for member in members:
   if not member.isreg() or member.issym() or member.size<1 or member.size>1048576: fail("bootstrap bundle member is unsafe")
   payload=archive.extractfile(member)
   if payload is None: fail("bootstrap bundle member is unreadable")
   destination=target/member.name; secure_dir(destination.parent)
   with destination.open("xb") as output:
    while True:
     chunk=payload.read(65536)
     if not chunk: break
     output.write(chunk)
   os.chmod(destination,0o600)
root=pathlib.Path(sys.argv[1]); campaign=sys.argv[2]
bundle_url,bundle_hash,bundle_bytes=sys.argv[3],sys.argv[4],int(sys.argv[5])
manifest_url,manifest_hash,manifest_bytes=sys.argv[6],sys.argv[7],int(sys.argv[8])
urlmap_url,urlmap_hash,urlmap_bytes=sys.argv[9],sys.argv[10],int(sys.argv[11])
campaign_root=root/campaign; secure_dir(campaign_root)
bundle=campaign_root/"receiver.tar.gz"; sealed=campaign_root/"sealed-manifest.json"; urlmap=campaign_root/"presigned-urls.json"; receiver=campaign_root/"receiver"
fetch(bundle_url,bundle_hash,bundle_bytes,bundle); fetch(manifest_url,manifest_hash,manifest_bytes,sealed); fetch(urlmap_url,urlmap_hash,urlmap_bytes,urlmap); extract_bundle(bundle,receiver)
result=subprocess.run(["/usr/bin/python3","-I","-B",str(receiver/"run_receiver.py"),"--manifest",str(sealed),"--signing-public-key",str(receiver/"signing-public.key"),"--url-map",str(urlmap)],capture_output=True,text=True,timeout=7200)
sys.stdout.write(result.stdout); sys.stderr.write(result.stderr); raise SystemExit(result.returncode)
'''


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EmergencyBootstrapError("Emergency bootstrap descriptor has a duplicate field")
        result[key] = value
    return result


def _artifact(value: object, *, label: str, maximum_bytes: int) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"url", "sha256", "bytes"}:
        raise EmergencyBootstrapError(f"{label} descriptor fields are invalid")
    url = value.get("url")
    if not isinstance(url, str) or not url or len(url.encode("utf-8")) > 16 * 1024:
        raise EmergencyBootstrapError(f"{label} URL is invalid")
    try:
        _validate_object_storage_url(url, label=label)
    except Exception as exc:
        raise EmergencyBootstrapError(f"{label} URL is not an approved Arvan Object Storage URL") from exc
    if not isinstance(value.get("sha256"), str) or SHA256_RE.fullmatch(value["sha256"]) is None:
        raise EmergencyBootstrapError(f"{label} SHA-256 is invalid")
    size = value.get("bytes")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= maximum_bytes:
        raise EmergencyBootstrapError(f"{label} byte count is invalid")
    return dict(value)


def load_descriptor(path: Path) -> dict[str, Any]:
    try:
        state = path.lstat()
        if (
            not stat.S_ISREG(state.st_mode)
            or stat.S_ISLNK(state.st_mode)
            or state.st_uid != os.geteuid()
            or state.st_nlink != 1
            or stat.S_IMODE(state.st_mode) != 0o600
            or not 1 <= state.st_size <= MAX_DESCRIPTOR_BYTES
        ):
            raise EmergencyBootstrapError("Emergency bootstrap descriptor must be one root-only 0600 regular file")
        payload = json.loads(
            read_secure_text(path, label="Emergency bootstrap descriptor", max_size=MAX_DESCRIPTOR_BYTES),
            object_pairs_hook=_strict_object,
        )
    except EmergencyBootstrapError:
        raise
    except Exception as exc:
        raise EmergencyBootstrapError("Emergency bootstrap descriptor is unavailable or invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "campaign_id", "expires_in_seconds", "receiver_bundle", "manifest", "url_map"
    }:
        raise EmergencyBootstrapError("Emergency bootstrap descriptor fields are invalid")
    campaign_id = payload.get("campaign_id")
    if not isinstance(campaign_id, str) or CAMPAIGN_RE.fullmatch(campaign_id) is None:
        raise EmergencyBootstrapError("Emergency bootstrap campaign identity is invalid")
    expires = payload.get("expires_in_seconds")
    if isinstance(expires, bool) or not isinstance(expires, int) or not 60 <= expires <= 900:
        raise EmergencyBootstrapError("Emergency bootstrap lifetime is invalid")
    if payload.get("schema") != SCHEMA:
        raise EmergencyBootstrapError("Emergency bootstrap schema is invalid")
    payload["receiver_bundle"] = _artifact(
        payload["receiver_bundle"], label="receiver bundle", maximum_bytes=MAX_RECEIVER_BUNDLE_BYTES
    )
    payload["manifest"] = _artifact(
        payload["manifest"], label="sealed manifest", maximum_bytes=MAX_SEALED_MANIFEST_BYTES
    )
    payload["url_map"] = _artifact(
        payload["url_map"], label="presigned URL map", maximum_bytes=MAX_URL_MAP_BYTES
    )
    return payload


def confirmation_phrase(payload: Mapping[str, Any]) -> str:
    return f"receive-emergency-ir:{payload['campaign_id']}:{payload['manifest']['sha256']}"


def remote_command(payload: Mapping[str, Any]) -> str:
    bundle = payload["receiver_bundle"]
    sealed = payload["manifest"]
    url_map = payload["url_map"]
    arguments = (
        "/usr/bin/python3", "-I", "-B", "-c", REMOTE_BOOTSTRAP,
        REMOTE_ROOT, str(payload["campaign_id"]),
        str(bundle["url"]), str(bundle["sha256"]), str(bundle["bytes"]),
        str(sealed["url"]), str(sealed["sha256"]), str(sealed["bytes"]),
        str(url_map["url"]), str(url_map["sha256"]), str(url_map["bytes"]),
    )
    command = "sudo -n -- " + " ".join(shlex.quote(item) for item in arguments)
    if len(command.encode("utf-8")) > 65_536:
        raise EmergencyBootstrapError("Emergency bootstrap SSH command exceeds its fixed bound")
    return command


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.host != WA_IR_HOST or args.port != 22 or args.user != WA_IR_USER:
        raise EmergencyBootstrapError("WA-IR SSH target differs from the pinned Emergency host")
    try:
        identity = args.identity.lstat()
    except OSError as exc:
        raise EmergencyBootstrapError("WA-IR SSH identity is unavailable") from exc
    if (
        not stat.S_ISREG(identity.st_mode)
        or identity.st_uid != os.geteuid()
        or identity.st_nlink != 1
        or stat.S_IMODE(identity.st_mode) != 0o600
    ):
        raise EmergencyBootstrapError("WA-IR SSH identity must be one root-only 0600 regular file")
    payload = load_descriptor(args.descriptor)
    expected_confirmation = confirmation_phrase(payload)
    if not args.apply:
        return {
            "status": "planned",
            "campaign_id": payload["campaign_id"],
            "required_confirmation": expected_confirmation,
            "payload_transport": "private-arvan-object-storage-only",
            "ssh_payload_transfer": False,
        }
    if args.confirm != expected_confirmation:
        raise EmergencyBootstrapError("Emergency receive confirmation mismatch")
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "LogLevel=ERROR", "-p", str(args.port),
        "-i", str(args.identity), f"{args.user}@{args.host}", remote_command(payload),
    ]
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=7500)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyBootstrapError("Emergency Object Storage receive command failed") from exc
    if completed.returncode != 0:
        raise EmergencyBootstrapError("Emergency Object Storage receive command failed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise EmergencyBootstrapError("Emergency Object Storage receive returned non-JSON output") from exc
    if result.get("status") != "received-non-authorizing" or result.get("campaign_id") != payload["campaign_id"]:
        raise EmergencyBootstrapError("Emergency Object Storage receive did not return the pinned success state")
    return {
        "status": "verified-received-non-authorizing",
        "campaign_id": payload["campaign_id"],
        "manifest_sha256": result.get("manifest_sha256"),
        "artifact_count": len(result.get("artifacts", [])),
        "payload_transport": "private-arvan-object-storage-only",
        "ssh_payload_transfer": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--host", default=WA_IR_HOST)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default=WA_IR_USER)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = execute(args)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
