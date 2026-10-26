"""Pure F79 rebase lineage, candidate effective paths, and fixed old-FD proof."""
from hashlib import sha256
from pathlib import Path
import gzip,io,json,os,re,time,zipfile

BASELINE='sha256:f79cdda21c192c52536f4ccfecadc7f724453a9ea4abfae96b1efe540ebbc61b'
PARENT='sha256:2c6ddd332f0aac17613208e66980e0154933bcb23cf2b94bd3d228c3ad5fd4e7'
OLD_ROOTFS=['sha256:'+v for v in ('1d69a5fd31932841d7825ef4780c06f008eea65aaa9f3110fe09d5832ed5c7d8',
 'b04faa200fd3e9f30040cf3c40ff77c01c85d517ddfb74b5fb3c27e71216e2a1','155a0d2cc3cb8d1948825bf4f73a22597780c8a87efc7e6927ba3b69840ac195',
 'e976e5e29e2fe118de00b078e756776953e3345cd39ab2f8dcc48aa809f42305','de0a4d9f20d1269261f430addd5e62777dff9bde6fa3abdd32b40b0cbc03a48e',
 '5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef','fc872c0e1fed4ac37483fbc7ae6df96273acd00add680262c3757dc0623e71ad',
 '07f5ee5029833c61e05944bb657108c0a0eb1824316569dcaec85de8557fd982')]
PROFILE='f79cdda21c19'
FILES={'/app/core/market_intelligence/private_capture.py':(111954,'1b9c611692978edec4c37de217e77abf00f5a0e456006060c936d40c53978929'),
 '/app/core/market_intelligence/private_pipeline_foundation.py':(37187,'f9d2b55921431e069b06e7de463d8b1d36ecbc01a94d5d0d98cd59f1dc9260da')}
ANCESTORS=('/','/app','/app/core','/app/core/market_intelligence')
SCHEMA='normal_market_rebased_public_sources/1'
RESOLVER_SHA='24f0f2e49f2052c1425e60cbb72bfd24bc5d14af44a9646751effb0c4d019236'
PAYLOAD_SHA='22f210c78db27312ec96e45ef15d279e88d0cbd86c97af5d830a1e719e6fd6b2'
REPLACEMENT={'bytes':47421,'sha256':'f2aa2b3474df2e05275900780cb06b56365203c8e439645a9dc680fcb6c3aea7'}
def need(ok,code):
 if not ok:raise RuntimeError(code)
def same(a,b):
 if type(a) is not type(b):return False
 if type(a) is dict:return set(a)==set(b) and all(same(a[k],b[k]) for k in a)
 if type(a) is list:return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
def document(raw):
 need(type(raw) is bytes and 0<len(raw)<=2*1024**2,'rebase_json_bound')
 def unique(rows):
  result={}
  for key,value in rows:need(key not in result,'rebase_json_duplicate');result[key]=value
  return result
 return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'rebase_json_constant'))
def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()

def admit_lineage(raw,spec):
 """Authenticate the actual original COPY records, never infer an old prefix."""
 row=spec['evidence_artifact'];need(spec['profile']==PROFILE and len(raw)==row['size_in_bytes'] and sha256(raw).hexdigest()==row['digest'][7:],'rebase_evidence_zip')
 with zipfile.ZipFile(io.BytesIO(raw)) as archive:
  infos=archive.infolist();need(len(infos)<=64 and len({i.filename for i in infos})==len(infos),'rebase_evidence_inventory')
  def read(name,maximum):
   item=archive.getinfo(name);need(not item.is_dir() and not item.flag_bits&1 and 0<item.file_size<=maximum,'rebase_evidence_member');return archive.read(item)
  export_raw=read('image-export.json',65536);need(len(export_raw)==spec['public_export_bytes'] and sha256(export_raw).hexdigest()==spec['public_export_sha256'],'rebase_export')
  exported=document(export_raw);payload_raw=read('reports/profile-payload.json',2*1024**2);composition_raw=read('reports/composition.json',2*1024**2)
 payload=document(payload_raw);composition=document(composition_raw)
 need(sha256(payload_raw).hexdigest()==PAYLOAD_SHA==exported['payload_sha256']==composition['payload_sha256']
  and sha256(composition_raw).hexdigest()==exported['composition_sha256'],'rebase_copy_record_binding')
 need(payload['schema']=='market_security_profile_payload/1' and payload['profile']==composition['profile']==exported['profile']==PROFILE
  and payload['baseline_image_id']==BASELINE and composition['parent_image_id']==exported['parent_image_id']==PARENT
  and composition['image_id']==exported['image_id']==spec['candidate_config_id'],'rebase_image_lineage')
 need(all(type(v) is bool and v is False for v in (payload['runtime_environment_read'],payload['security_accepted'],payload['deployment_eligible'],
  composition['security_accepted'],composition['runtime_data_used'],exported['security_accepted'],exported['runtime_data_used'],exported['deployment_performed'])),'rebase_false_claims')
 need(payload['image_input_signature']==composition['image_input_signature']==exported['image_input_signature']==spec['candidate_input_signature']
  and payload['baseline_input_signature']==composition['baseline_input_signature']==exported['baseline_input_signature'],'rebase_input_lineage')
 layers=composition['candidate_layers'];parent=composition['parent_layers']
 need(same(layers,spec['candidate_rootfs']) and type(parent) is list and len(parent)==18 and len(layers)==19
  and same(layers[:18],parent),'rebase_composition_layers')
 overlay=payload['overlay_inputs'];need(overlay['schema']=='market_security_profile_overlay_inputs/1' and overlay['parent_image_id']==PARENT
  and same(overlay['product_files'],payload['files']) and same(overlay['replacement_program'],REPLACEMENT),'rebase_replacement_lineage')
 need(type(payload['files']) is list and len(payload['files'])==93 and len({v['path'] for v in payload['files']})==93,'rebase_copy_inventory')
 selected={}
 for target,(size,pin) in FILES.items():
  matches=[r for r in payload['files'] if r['path']==target[5:]]
  need(len(matches)==1,'rebase_fixed_copy');record=matches[0]
  need(set(record)=={'path','mode','bytes','sha256','git_blob'} and record['mode']=='100644'
   and type(record['bytes']) is int and record['bytes']==size and record['sha256']==pin
   and type(record['git_blob']) is str and re.fullmatch('[a-f0-9]{40}',record['git_blob']),'rebase_fixed_copy')
  selected[target]=record
 return {'schema':'f79_rebased_payload_lineage/1','baseline_image_id':BASELINE,'composition_parent_image_id':PARENT,
  'composition_parent_rootfs':parent,'candidate_config_id':spec['candidate_config_id'],'candidate_rootfs':layers,
  'evidence_zip_sha256':sha256(raw).hexdigest(),'export_sha256':sha256(export_raw).hexdigest(),'payload_sha256':PAYLOAD_SHA,
  'composition_sha256':sha256(composition_raw).hexdigest(),'replacement_program':REPLACEMENT,'fixed_files':selected,
  'baseline_input_signature':payload['baseline_input_signature'],'image_input_signature':payload['image_input_signature']}

def resolve_candidate(fd,size,end,data,D,T,moby,M,lineage,identity,association,check):
 """Full physical graph, then fixed paths across all candidate layers only."""
 M.source_association(association)
 need(data['spec']['profile']==PROFILE and same(data['spec']['layers'],lineage['candidate_rootfs'])
  and data['spec']['config_id']==lineage['candidate_config_id'],'rebase_candidate_binding')
 need(list(D.identity(os.fstat(fd)))==identity and identity[2:7]==[0o100600,0,0,1,size],'rebase_archive_identity');check()
 with os.fdopen(os.dup(fd),'rb') as stream:
  graph=D.validate_archive(stream,size,end,data,T,moby);check()
  need(graph['archive_sha256']==association['archive_sha256'] and graph['config_id'][7:]==association['candidate_config_sha256'],'rebase_archive_association')
  members,_=T.inventory(T.ArchiveReader(stream,size,end));offset,length=members['blobs/sha256/'+graph['manifest_id'][7:]]
  need(0<length<=D.MAX_JSON,'rebase_manifest_bound');manifest=D.document(os.pread(fd,length,offset));state={};expanded=0
  class Slice:
   def __init__(self,start,length):self.start,self.length,self.offset=start,length,0
   def read(self,n):
    need(type(n) is int and n>=0 and time.monotonic()<end,'rebase_layer_bound')
    raw=os.pread(fd,min(n,65536,self.length-self.offset),self.start+self.offset);self.offset+=len(raw);return raw
  need(len(manifest['layers'])==len(data['spec']['layers']),'rebase_layer_count')
  for diff,descriptor in zip(data['spec']['layers'],manifest['layers']):
   start,length=members['blobs/sha256/'+descriptor['digest'][7:]];source=Slice(start,length)
   if D.MEDIA[descriptor['mediaType']]:
    with gzip.GzipFile(fileobj=source) as decoded:state,count,digest=M.source_layer(decoded,state,FILES,end)
   else:state,count,digest=M.source_layer(source,state,FILES,end)
   expanded+=count;need(expanded<=512*1024**2 and 'sha256:'+digest==diff,'rebase_diff_id');check()
  need(expanded==graph['expanded_layer_bytes'] and D.digest_fd(fd,size,end)==graph['archive_sha256'],'rebase_expansion')
 expected={}
 for target,(length,pin) in FILES.items():
  row=state.get(target[1:]);need(type(row) is dict and row['bytes']==length and row['sha256']==pin,'rebase_effective_source')
  expected[target]={'kind':'file','bytes':length,'sha256':pin,'metadata':row['metadata']}
 for target in ANCESTORS:
  row=state.get(target[1:]);need(type(row) is dict and row.get('directory') is True,'rebase_effective_ancestor')
  expected[target]={'kind':'directory','metadata':row['metadata']}
 for target,row in expected.items():
  meta=row['metadata'];need(type(meta) is list and len(meta)==4 and all(type(x) is int for x in meta[:3])
   and 0<=meta[0]<=0o7777 and meta[1]>=0 and meta[2]>=0 and meta[3]==[],'rebase_metadata_xattrs')
  if row['kind']=='file':need(meta[:3]==[0o644,10001,10001],'rebase_effective_source_metadata')
 need(list(D.identity(os.fstat(fd)))==identity,'rebase_archive_identity');check()
 return {'physical':graph,'archive_bytes':size,'expected':expected,'tar_identity':identity}

RUNTIME_FORMAT='{"id":{{json .Id}},"image":{{json .Image}},"running":{{json .State.Running}},"restarting":{{json .State.Restarting}},"pid":{{json .State.Pid}},"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}}}'
def observe_old_fixed(query,cid,image,expected):
 need(image==BASELINE and type(cid) is str and re.fullmatch('[a-f0-9]{64}',cid) and set(expected)==set(FILES)|set(ANCESTORS),'rebase_old_scope')
 def runtime():
  value=document(query(['container','inspect','--format',RUNTIME_FORMAT,cid]));need(set(value)=={'id','image','running','restarting','pid','started','restarts'}
   and value['id']==cid and value['image']==BASELINE and value['running'] is True and value['restarting'] is False
   and type(value['pid']) is int and value['pid']>0 and type(value['restarts']) is int and value['restarts']>=0
   and type(value['started']) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z',value['started']),'rebase_old_runtime');return value
 before=runtime()
 program='''import os,stat,json
from hashlib import sha256
expected=EXPECTED
def ident(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
held=[];files={};ancestors={}
try:
 for path,row in sorted(expected.items()):
  assert os.path.realpath(path)==path,'fixed_public_source_refused'
  directory=row['kind']=='directory';flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC
  if directory:flags|=os.O_DIRECTORY
  before=ident(os.lstat(path))
  assert (stat.S_ISDIR(before[2]) if directory else stat.S_ISREG(before[2]) and before[5]==1),'fixed_public_source_refused'
  assert [stat.S_IMODE(before[2]),before[3],before[4],[]]==row['metadata'],'fixed_public_source_refused'
  if not directory:assert before[6]==row['bytes'] and 0<row['bytes']<=131072,'fixed_public_source_refused'
  fd=os.open(path,flags);held.append((path,fd,before));assert before==ident(os.fstat(fd)),'fixed_public_source_refused'
  assert os.listxattr(fd)==[],'fixed_public_source_refused'
  if directory:ancestors[path]={'identity':before,'xattrs_absent':True}
  else:
   size=row['bytes'];assert before[6]==size and 0<size<=131072,'fixed_public_source_refused'
   raw=os.pread(fd,size+1,0);assert len(raw)==size and sha256(raw).hexdigest()==row['sha256'],'fixed_public_source_refused'
   files[path]={'identity':before,'bytes':size,'sha256':row['sha256'],'xattrs_absent':True}
 for path,fd,before in held:assert before==ident(os.fstat(fd))==ident(os.lstat(path)) and os.path.realpath(path)==path,'fixed_public_source_refused'
 print(json.dumps({'schema':'f79_rebased_old_fixed_fd/1','files':files,'ancestors':ancestors,'metadata_equal_in_ram':True},sort_keys=True))
finally:
 for path,fd,before in reversed(held):os.close(fd)
'''.replace('EXPECTED',repr(expected),1)
 value=document(query(['exec',cid,'/usr/local/bin/python','-I','-B','-c',program]))
 need(type(value) is dict and set(value)=={'schema','files','ancestors','metadata_equal_in_ram'} and value['schema']=='f79_rebased_old_fixed_fd/1'
  and value['metadata_equal_in_ram'] is True and set(value['files'])==set(FILES) and set(value['ancestors'])==set(ANCESTORS),'rebase_old_observation')
 for target,row in {**value['files'],**value['ancestors']}.items():
  meta=row['identity'];wanted=expected[target];need(type(meta) is list and len(meta)==9 and all(type(x) is int for x in meta)
   and meta[0]>0 and meta[1]>0 and [meta[2]&0o7777,meta[3],meta[4],[]]==wanted['metadata'] and row['xattrs_absent'] is True,'rebase_old_metadata')
  need(stat_type(meta[2])==(0o040000 if wanted['kind']=='directory' else 0o100000),'rebase_old_type')
  need(set(row)==({'identity','bytes','sha256','xattrs_absent'} if wanted['kind']=='file' else {'identity','xattrs_absent'}),'rebase_old_shape')
  if wanted['kind']=='file':need(meta[5]==1 and meta[6]==row['bytes']==wanted['bytes'] and row['sha256']==wanted['sha256'],'rebase_old_source')
 need(same(before,runtime()),'rebase_old_runtime');return {'container_id':cid,'image_id':image,'runtime':before,**value}
def stat_type(mode):return mode&0o170000

def proof(lineage,resolved,old,old_rootfs,association,producer_sha):
 need(same(old_rootfs,OLD_ROOTFS)
  and not same(old_rootfs,lineage['candidate_rootfs'][:len(old_rootfs)]) and old['image_id']==BASELINE,'rebase_old_layers')
 need(old.get('schema')=='f79_rebased_old_fixed_fd/1' and old.get('metadata_equal_in_ram') is True,'rebase_old_observation')
 need(type(producer_sha) is str and re.fullmatch('[a-f0-9]{64}',producer_sha) and association['producer_sha256']==producer_sha,'rebase_producer')
 graph=resolved['physical']
 need(graph['archive_sha256']==association['archive_sha256'] and graph['config_id']=='sha256:'+association['candidate_config_sha256']
  and graph['config_id']==lineage['candidate_config_id'] and resolved['archive_bytes']==resolved['tar_identity'][6],'rebase_archive_association')
 expected=resolved['expected'];need(set(expected)==set(FILES)|set(ANCESTORS) and set(old['files'])==set(FILES)
  and set(old['ancestors'])==set(ANCESTORS),'rebase_old_observation')
 for path,row in expected.items():
  actual=(old['files'] if path in FILES else old['ancestors'])[path];meta=actual['identity']
  need(type(meta) is list and len(meta)==9 and all(type(v) is int for v in meta) and actual['xattrs_absent'] is True
   and same([meta[2]&0o7777,meta[3],meta[4],[]],row['metadata'])
   and stat_type(meta[2])==(0o100000 if path in FILES else 0o040000),'rebase_old_metadata')
  if path in FILES:need(meta[5]==1 and meta[6]==actual['bytes']==row['bytes'] and actual['sha256']==row['sha256'],'rebase_old_source')
 return {'schema':SCHEMA,'composition_model':'authenticated_rebase_and_profile_copy','baseline_image_id':BASELINE,'baseline_rootfs':old_rootfs,
  'candidate_config_id':lineage['candidate_config_id'],'candidate_rootfs':lineage['candidate_rootfs'],'lineage':lineage,
  'archive_bytes':resolved['archive_bytes'],'archive_sha256':graph['archive_sha256'],'tar_identity':resolved['tar_identity'],
  'source_identities':graph['source_identities'],'association':association,'resolver_sha256':RESOLVER_SHA,
  'candidate_effective_metadata':resolved['expected'],'old_fixed_observation':old,'physical_graph_closed':True,
  'ancestor_metadata_equal_in_ram':True,'configuration_values_disclosed':False,'security_accepted':False,'activation_authority':False}
