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
from typing import Any, Mapping
from urllib.parse import urlsplit


# v2 requires the controller descriptor to carry the signed bootstrap
# provenance.  Existing v1 descriptors therefore fail closed rather than
# silently launching a bundle without a provenance check.
SCHEMA = "gold-trade-emergency-ir-object-storage-bootstrap-v2"
WA_IR_HOST = "95.38.164.29"
WA_IR_USER = "ubuntu"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
SIGNER_KEY_ID_RE = re.compile(r"^ed25519-sha256:[a-f0-9]{64}$", re.ASCII)
GIT_REVISION_RE = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$", re.ASCII)
CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
MAX_DESCRIPTOR_BYTES = 128 * 1024
MAX_RECEIVER_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_SEALED_MANIFEST_BYTES = 128 * 1024
MAX_URL_MAP_BYTES = 64 * 1024
REMOTE_ROOT = "/run/trading-bot-emergency-bootstrap"
ARVAN_OBJECT_STORAGE_HOST = "s3.ir-thr-at1.arvanstorage.ir"
BOOTSTRAP_PROVENANCE_SCHEMA = "gold-trade-emergency-ir-bootstrap-provenance-v1"
BOOTSTRAP_PROVENANCE_FIELDS = frozenset(
    {
        "schema",
        "publisher_source_revision",
        "receiver_bundle_sha256",
        "receiver_bundle_bytes",
        "signer_key_id",
    }
)


class EmergencyBootstrapError(RuntimeError):
    pass


def _validate_object_storage_url(url: str, *, label: str) -> None:
    """Accept only HTTPS URLs for Arvan's fixed Object Storage data plane."""

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise EmergencyBootstrapError(f"{label} URL is malformed") from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not (hostname == ARVAN_OBJECT_STORAGE_HOST or hostname.endswith("." + ARVAN_OBJECT_STORAGE_HOST))
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or parsed.fragment
    ):
        raise EmergencyBootstrapError(f"{label} URL is not an approved Arvan Object Storage URL")


def _read_private_descriptor(path: Path) -> str:
    """Read the one bounded root-only descriptor without a three-site helper."""

    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 1 <= before.st_size <= MAX_DESCRIPTOR_BYTES
        ):
            raise EmergencyBootstrapError("Emergency bootstrap descriptor must be one root-only 0600 regular file")
        payload = bytearray()
        while len(payload) <= MAX_DESCRIPTOR_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_DESCRIPTOR_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if len(payload) != before.st_size or len(payload) > MAX_DESCRIPTOR_BYTES or any(
            getattr(before, field) != getattr(after, field) for field in fields
        ):
            raise EmergencyBootstrapError("Emergency bootstrap descriptor changed while being read")
        return bytes(payload).decode("utf-8")
    except EmergencyBootstrapError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise EmergencyBootstrapError("Emergency bootstrap descriptor is unavailable or invalid") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


# This small program is passed as a command, never as a release payload.  It
# downloads all files itself over HTTPS and accepts only a fixed, tiny archive
# layout.  The archive's hash arrives in the root-only descriptor and is
# checked before its pinned public key is used to verify the sealed manifest.
REMOTE_BOOTSTRAP = r'''import base64,hashlib,json,os,pathlib,re,ssl,stat,subprocess,sys,tarfile,urllib.error,urllib.request
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
def existing(target,digest,size):
 target=pathlib.Path(target)
 try: before=target.lstat()
 except FileNotFoundError: return False
 if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_nlink!=1 or stat.S_IMODE(before.st_mode)&0o077 or before.st_size!=size: fail("existing bootstrap artifact is unsafe")
 observed=hashlib.sha256(); total=0
 with target.open("rb") as source:
  while True:
   chunk=source.read(65536)
   if not chunk: break
   total+=len(chunk)
   if total>size: fail("existing bootstrap artifact exceeds its sealed size")
   observed.update(chunk)
 after=target.lstat()
 if before.st_dev!=after.st_dev or before.st_ino!=after.st_ino or before.st_size!=after.st_size or observed.hexdigest()!=digest or total!=size: fail("existing bootstrap artifact differs from its sealed hash")
 return True
def fetch(url,digest,size,target):
 target=pathlib.Path(target); secure_dir(target.parent)
 if existing(target,digest,size): return
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
  os.chmod(temporary,0o600)
  try: os.link(temporary,target,follow_symlinks=False)
  except FileExistsError: fail("refusing to overwrite bootstrap artifact")
  temporary.unlink()
 except Exception:
  try: temporary.unlink()
  except FileNotFoundError: pass
  raise
def bundle_ready(target):
 target=pathlib.Path(target)
 try: state=target.lstat()
 except FileNotFoundError: return False
 if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode) or state.st_uid!=0 or stat.S_IMODE(state.st_mode)&0o022: fail("existing receiver bundle directory is unsafe")
 allowed={"run_receiver.py","signing-public.key","scripts/emergency_ir_object_storage_manifest.py","scripts/emergency_ir_object_storage_receiver.py","scripts/emergency_ir_standalone_activate.py"}
 actual={str(item.relative_to(target)) for item in target.rglob("*") if item.is_file()}
 if actual!=allowed: fail("existing receiver bundle is incomplete")
 for name in allowed:
  item=target/name; member=item.lstat()
  if stat.S_ISLNK(member.st_mode) or not stat.S_ISREG(member.st_mode) or member.st_uid!=0 or stat.S_IMODE(member.st_mode)&0o077: fail("existing receiver bundle member is unsafe")
 return True
def extract_bundle(bundle,target):
 allowed={"run_receiver.py","signing-public.key","scripts/emergency_ir_object_storage_manifest.py","scripts/emergency_ir_object_storage_receiver.py","scripts/emergency_ir_standalone_activate.py"}
 if bundle_ready(target): return
 temporary=target.with_name("."+target.name+"."+str(os.getpid())+".extract")
 secure_dir(temporary)
 with tarfile.open(bundle,"r:gz") as archive:
  members=archive.getmembers()
  if {member.name for member in members}!=allowed or len(members)!=len(allowed): fail("bootstrap bundle layout is invalid")
  for member in members:
   if not member.isreg() or member.issym() or member.size<1 or member.size>1048576: fail("bootstrap bundle member is unsafe")
   payload=archive.extractfile(member)
   if payload is None: fail("bootstrap bundle member is unreadable")
   destination=temporary/member.name; secure_dir(destination.parent)
   with destination.open("xb") as output:
    while True:
     chunk=payload.read(65536)
     if not chunk: break
     output.write(chunk)
   os.chmod(destination,0o600)
 try: os.rename(temporary,target)
 except FileExistsError: fail("refusing to overwrite receiver bundle directory")
def bundled_key_id(path):
 try:
  encoded=pathlib.Path(path).read_bytes().decode("ascii").strip()
  key=base64.b64decode(encoded.encode("ascii"),validate=True)
 except Exception: fail("receiver bundle signing public key is invalid")
 if len(key)!=32: fail("receiver bundle signing public key is invalid")
 return "ed25519-sha256:"+hashlib.sha256(key).hexdigest()
root=pathlib.Path(sys.argv[1]); campaign=sys.argv[2]
bundle_url,bundle_hash,bundle_bytes=sys.argv[3],sys.argv[4],int(sys.argv[5])
manifest_url,manifest_hash,manifest_bytes=sys.argv[6],sys.argv[7],int(sys.argv[8])
urlmap_url,urlmap_hash,urlmap_bytes=sys.argv[9],sys.argv[10],int(sys.argv[11])
source_revision,bundle_provenance_hash,bundle_provenance_bytes,expected_signer_key_id=sys.argv[12],sys.argv[13],int(sys.argv[14]),sys.argv[15]
if re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})",source_revision) is None: fail("bootstrap publisher source revision is invalid")
if re.fullmatch(r"[a-f0-9]{64}",bundle_provenance_hash) is None or not 1<=bundle_provenance_bytes<=4194304: fail("bootstrap receiver bundle provenance is invalid")
if re.fullmatch(r"ed25519-sha256:[a-f0-9]{64}",expected_signer_key_id) is None: fail("bootstrap signer key identity is invalid")
if bundle_hash!=bundle_provenance_hash or bundle_bytes!=bundle_provenance_bytes: fail("receiver bundle differs from descriptor provenance")
campaign_root=root/campaign; secure_dir(campaign_root)
bundle=campaign_root/"receiver.tar.gz"; sealed=campaign_root/"sealed-manifest.json"; urlmap=campaign_root/"presigned-urls.json"; receiver=campaign_root/("receiver-"+bundle_hash)
fetch(bundle_url,bundle_hash,bundle_bytes,bundle); fetch(manifest_url,manifest_hash,manifest_bytes,sealed); fetch(urlmap_url,urlmap_hash,urlmap_bytes,urlmap); extract_bundle(bundle,receiver)
if bundled_key_id(receiver/"signing-public.key")!=expected_signer_key_id: fail("receiver bundle signing public key does not match descriptor")
result=subprocess.run(["/usr/bin/python3","-I","-B",str(receiver/"run_receiver.py"),"--manifest",str(sealed),"--signing-public-key",str(receiver/"signing-public.key"),"--url-map",str(urlmap),"--expected-publisher-source-revision",source_revision,"--expected-receiver-bundle-sha256",bundle_provenance_hash,"--expected-receiver-bundle-bytes",str(bundle_provenance_bytes),"--expected-signer-key-id",expected_signer_key_id],capture_output=True,text=True,timeout=7200)
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
    _validate_object_storage_url(url, label=label)
    if not isinstance(value.get("sha256"), str) or SHA256_RE.fullmatch(value["sha256"]) is None:
        raise EmergencyBootstrapError(f"{label} SHA-256 is invalid")
    size = value.get("bytes")
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= maximum_bytes:
        raise EmergencyBootstrapError(f"{label} byte count is invalid")
    return dict(value)


def _bootstrap_provenance(value: object) -> dict[str, Any]:
    """Validate the descriptor's signed-bootstrap identity without imports.

    The controller runs before the fetched bundle exists, so it repeats the
    narrow wire contract locally.  The bundled verifier later checks the same
    value against the signed manifest before accepting campaign artifacts.
    """

    if not isinstance(value, dict) or set(value) != BOOTSTRAP_PROVENANCE_FIELDS:
        raise EmergencyBootstrapError("Emergency bootstrap provenance fields are invalid")
    if value.get("schema") != BOOTSTRAP_PROVENANCE_SCHEMA:
        raise EmergencyBootstrapError("Emergency bootstrap provenance schema is invalid")
    revision = value.get("publisher_source_revision")
    if not isinstance(revision, str) or GIT_REVISION_RE.fullmatch(revision) is None:
        raise EmergencyBootstrapError("Emergency bootstrap publisher source revision is invalid")
    bundle_hash = value.get("receiver_bundle_sha256")
    if not isinstance(bundle_hash, str) or SHA256_RE.fullmatch(bundle_hash) is None:
        raise EmergencyBootstrapError("Emergency bootstrap receiver bundle hash is invalid")
    bundle_bytes = value.get("receiver_bundle_bytes")
    if isinstance(bundle_bytes, bool) or not isinstance(bundle_bytes, int) or not 1 <= bundle_bytes <= MAX_RECEIVER_BUNDLE_BYTES:
        raise EmergencyBootstrapError("Emergency bootstrap receiver bundle byte count is invalid")
    signer_key_id = value.get("signer_key_id")
    if not isinstance(signer_key_id, str) or SIGNER_KEY_ID_RE.fullmatch(signer_key_id) is None:
        raise EmergencyBootstrapError("Emergency bootstrap signer key identity is invalid")
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
        payload = json.loads(_read_private_descriptor(path), object_pairs_hook=_strict_object)
    except EmergencyBootstrapError:
        raise
    except Exception as exc:
        raise EmergencyBootstrapError("Emergency bootstrap descriptor is unavailable or invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "campaign_id", "expires_in_seconds", "bootstrap_provenance", "receiver_bundle", "manifest", "url_map"
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
    payload["bootstrap_provenance"] = _bootstrap_provenance(payload["bootstrap_provenance"])
    payload["receiver_bundle"] = _artifact(
        payload["receiver_bundle"], label="receiver bundle", maximum_bytes=MAX_RECEIVER_BUNDLE_BYTES
    )
    payload["manifest"] = _artifact(
        payload["manifest"], label="sealed manifest", maximum_bytes=MAX_SEALED_MANIFEST_BYTES
    )
    payload["url_map"] = _artifact(
        payload["url_map"], label="presigned URL map", maximum_bytes=MAX_URL_MAP_BYTES
    )
    if (
        payload["receiver_bundle"]["sha256"] != payload["bootstrap_provenance"]["receiver_bundle_sha256"]
        or payload["receiver_bundle"]["bytes"] != payload["bootstrap_provenance"]["receiver_bundle_bytes"]
    ):
        raise EmergencyBootstrapError("Emergency bootstrap receiver bundle differs from its provenance")
    return payload


def confirmation_phrase(payload: Mapping[str, Any]) -> str:
    provenance = payload["bootstrap_provenance"]
    return (
        f"receive-emergency-ir:{payload['campaign_id']}:{payload['manifest']['sha256']}:"
        f"{provenance['receiver_bundle_sha256']}"
    )


def remote_command(payload: Mapping[str, Any]) -> str:
    bundle = payload["receiver_bundle"]
    sealed = payload["manifest"]
    url_map = payload["url_map"]
    provenance = payload["bootstrap_provenance"]
    arguments = (
        "/usr/bin/python3", "-I", "-B", "-c", REMOTE_BOOTSTRAP,
        REMOTE_ROOT, str(payload["campaign_id"]),
        str(bundle["url"]), str(bundle["sha256"]), str(bundle["bytes"]),
        str(sealed["url"]), str(sealed["sha256"]), str(sealed["bytes"]),
        str(url_map["url"]), str(url_map["sha256"]), str(url_map["bytes"]),
        str(provenance["publisher_source_revision"]),
        str(provenance["receiver_bundle_sha256"]),
        str(provenance["receiver_bundle_bytes"]),
        str(provenance["signer_key_id"]),
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
    if (
        result.get("status") != "received-non-authorizing"
        or result.get("campaign_id") != payload["campaign_id"]
        or result.get("manifest_sha256") != payload["manifest"]["sha256"]
    ):
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
