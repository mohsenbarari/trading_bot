"""Exact previous same-image835 original; no recensus or download on import."""
from hashlib import sha256
from io import BytesIO
import json,stat,zipfile
CODES=frozenset(('retained_inventory_source','retained_inventory_provider','retained_inventory_zip','retained_inventory_member'))
def need(ok,code):
 if not ok:raise RuntimeError(code)
def provider(value,pointer):
 need(type(value)is dict and value.get('id')==pointer['artifact_id']and type(value.get('id'))is int
  and value.get('name')==pointer['artifact_name']and value.get('size_in_bytes')==pointer['archive_bytes']
  and value.get('digest')=='sha256:'+pointer['archive_sha256']and value.get('expired')is False,'retained_inventory_provider')
 run=value.get('workflow_run');need(type(run)is dict and run.get('id')==pointer['run_id']and type(run.get('id'))is int
  and run.get('head_sha')==pointer['head_sha']and run.get('head_branch')==pointer['head_branch'],'retained_inventory_provider')
def original_member(raw,pointer):
 need(type(raw)is bytes and len(raw)==pointer['archive_bytes']and sha256(raw).hexdigest()==pointer['archive_sha256'],'retained_inventory_zip')
 with zipfile.ZipFile(BytesIO(raw))as z:
  rows=z.infolist();need(0<len(rows)<=64 and len({r.filename for r in rows})==len(rows)and sum(r.file_size for r in rows)<=32*1024**2,'retained_inventory_zip')
  row=z.getinfo(pointer['member']);need(not row.is_dir()and not row.flag_bits&1 and row.compress_type in(0,8)
   and stat.S_IFMT(row.external_attr>>16)in(0,stat.S_IFREG)and row.file_size==pointer['member_bytes']<=2*1024**2,'retained_inventory_member')
  with z.open(row)as stream:value=stream.read(row.file_size+1);need(len(value)==row.file_size and stream.read(1)==b'','retained_inventory_member')
 need(sha256(value).hexdigest()==pointer['member_sha256'],'retained_inventory_member');return value
def collect(ctx,old,pointer_raw,pin):
 need(type(pointer_raw)is bytes and (len(pointer_raw),sha256(pointer_raw).hexdigest())==pin,'retained_inventory_source')
 p=json.loads(pointer_raw);need(p.get('schema')=='current2389_retained835_artifact_pointer/1'and p.get('repository')=='mohsenbarari/trading_bot'
  and p.get('fresh_inventory_executed_by_this_cohort')is False and p.get('security_accepted')is False,'retained_inventory_source')
 raw=ctx.command(['/usr/bin/gh','api','repos/mohsenbarari/trading_bot/actions/artifacts/'+str(p['artifact_id'])],auth=True,maximum=32768)
 metadata=old.document(raw);provider(metadata,p);ctx.write(ctx.root/'reports/retained-inventory-provider.json',metadata)
 folder=ctx.root/'retained-inventory';folder.mkdir(mode=0o700);path=folder/'public-proof.zip';ctx.download(p['artifact_id'],path,p['archive_bytes'],p['archive_sha256'])
 with ctx.C.pinned(path,p['archive_bytes'])as(stream,size):
  need(size==p['archive_bytes'],'retained_inventory_zip');raw=stream.read(size+1);value=original_member(raw,p)
 ctx.check();return value
