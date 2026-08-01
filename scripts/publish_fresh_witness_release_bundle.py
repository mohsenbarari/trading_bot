#!/usr/bin/env python3
"""Publish exactly one age-encrypted fresh release bundle with read-back."""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys, tempfile, uuid
from pathlib import Path
from typing import Any
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path: sys.path.insert(0, str(REPO_ROOT))
import boto3
from botocore.exceptions import ClientError
from scripts.publish_wa_ir_object_storage_preflight import (
    ARVAN_ENDPOINT, ARVAN_REGION, AGE_RECIPIENT_RE, MAX_RELEASE_BYTES,
    PublicationError, create_release_bundle, encrypt, require_private_versioned_bucket,
    _hash_regular, _upload_and_readback,
)
from scripts.fresh_campaign_secure_io import read_secure_root_file

SHA = re.compile(r"^[0-9a-f]{40}$")
class FreshWitnessPublicationError(RuntimeError): pass
def _credentials(path: Path) -> tuple[str,str]:
    try: payload=json.loads(read_secure_root_file(path,label="Arvan credentials",expected_mode=0o600,max_size=16384))
    except Exception: raise FreshWitnessPublicationError("Arvan credentials are unavailable") from None
    if set(payload)!={"access_key","secret_key"} or any(not isinstance(payload[x],str) or not payload[x] for x in payload): raise FreshWitnessPublicationError("Arvan credentials are invalid")
    return payload["access_key"],payload["secret_key"]
def _client(path:Path):
    access,secret=_credentials(path)
    return boto3.client("s3",endpoint_url=ARVAN_ENDPOINT,region_name=ARVAN_REGION,aws_access_key_id=access,aws_secret_access_key=secret)
def _absent(client,*,bucket:str,key:str)->None:
    try: client.head_object(Bucket=bucket,Key=key)
    except ClientError as exc:
        if str(exc.response.get("Error",{}).get("Code")) in {"404","NoSuchKey","NotFound"}: return
        raise FreshWitnessPublicationError("Object Storage absence check failed") from None
    raise FreshWitnessPublicationError("fresh release object key already exists")
def execute(a:argparse.Namespace)->dict[str,Any]:
    if SHA.fullmatch(a.release_sha) is None or AGE_RECIPIENT_RE.fullmatch(a.recipient) is None: raise FreshWitnessPublicationError("release or recipient is invalid")
    prefix=str(a.prefix).strip("/")+"/"
    if ".." in Path(prefix).parts or not prefix.startswith("staging/three-site/"): raise FreshWitnessPublicationError("fresh object prefix is invalid")
    phrase=f"publish-fresh-witness-release:{a.release_sha}:{a.bucket}:{prefix.rstrip('/')}"
    if not a.apply: return {"status":"planned","object_count":1,"required_confirmation":phrase,"ssh_payload_transfer":False}
    if a.confirm!=phrase: raise FreshWitnessPublicationError("publication confirmation mismatch")
    client=_client(a.credentials); require_private_versioned_bucket(client,bucket=a.bucket)
    with tempfile.TemporaryDirectory(prefix="fresh-witness-release-") as raw:
        plain=Path(raw)/"release.bundle"; plain_hash,plain_size=create_release_bundle(a.repo.resolve(),a.release_sha,plain)
        cipher=Path(raw)/"release.bundle.age"; cipher_hash,cipher_size=encrypt(plain,cipher,a.recipient)
        key=f"{prefix}bootstrap/witness/release/{uuid.uuid4().hex}/{cipher_hash}.release.bundle.age"; _absent(client,bucket=a.bucket,key=key)
        obj=_upload_and_readback(client,bucket=a.bucket,key=key,source=cipher,metadata={"kind":"fresh-witness-release","release-sha":a.release_sha,"plaintext-sha256":plain_hash})
    result={"status":"published-and-readback-verified","object_count":1,"release_sha":a.release_sha,"bucket":a.bucket,"object":obj,"plaintext_sha256":plain_hash,"plaintext_bytes":plain_size,"recipient_sha256":hashlib.sha256((a.recipient+"\n").encode()).hexdigest(),"ssh_payload_transfer":False}
    a.output.write_bytes((json.dumps(result,sort_keys=True,indent=2)+"\n").encode()); os.chmod(a.output,0o600)
    return result
def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--repo",type=Path,required=True); p.add_argument("--release-sha",required=True); p.add_argument("--credentials",type=Path,required=True); p.add_argument("--recipient",required=True); p.add_argument("--bucket",required=True); p.add_argument("--prefix",required=True); p.add_argument("--output",type=Path,required=True); p.add_argument("--apply",action="store_true"); p.add_argument("--confirm"); a=p.parse_args(argv)
    try:
        if a.output.exists(): raise FreshWitnessPublicationError("publication evidence already exists")
        print(json.dumps(execute(a),sort_keys=True)); return 0
    except Exception as exc: print(json.dumps({"status":"blocked","error_class":type(exc).__name__},sort_keys=True)); return 1
if __name__=="__main__": raise SystemExit(main())
