"""Fresh normal-operation clock and initial UNASSESSED SQLite inventory.

No execution on import, source/object/link/DSO proof or CVE/activation authority.
The real caller, original FDs and unchanged pinned helpers remain mandatory.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import errno,fcntl,json,math,os,re,stat,tempfile,time

CLOCK_PRODUCER_QUALIFIED=True
HARD_SECONDS=1800
HEALTHY_SECONDS=1500
CLEANUP_RESERVE=300
MAX_EXPORT=2*1024**3
MAX_INVENTORY=4*1024**2
MAX_COMPONENT=128*1024
COHORT={
 'old':('deploy/maintenance/app_r2_observe02/run_app_image_ci_smoke.py',68163,'b2c958060d176c0098b86a44d0dc225d4494023120b3253239bef9e2f3f2acb2'),
 'E':('deploy/maintenance/app_gold2_smoke/export_controller_gold2.py',27863,'5480efd9fb0a5443e19262323821f917c57e2168f4aec6151bc44c27ecae95ad'),
 'A':('deploy/maintenance/app_gold2_smoke/archive_inventory_gold2.py',33083,'04a58e38792d9c5e11ac3ee37db593b6a624b059f0378d0ff2bf6aa560dff005'),
 'I':('deploy/maintenance/app_r2_observe02/inventory.py',27509,'e8171fb6d28a5b3e938e0136dab230c13a9e864f66aad4c2727878e22a3ea73f'),
 'C':('deploy/maintenance/app_r2_observe02/compose_security_os_candidate.py',60894,'4156ad042885bc7104c5a9ce537193adf2211ade8447e77676ad29d520e7633d'),
 'P':('deploy/maintenance/app_r2_observe02/compose_app_mountpoint_image.py',49042,'14849bd8c616b27ade1c8488b8e6f276b369480456d0acc1a8c8eb4284f40226'),
 'M':('deploy/maintenance/app_r2_observe02/compose_market_security_os_candidate.py',52231,'c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9'),
 'L':('deploy/maintenance/app_r2_observe02/admit_security_component_image_reuse.py',27518,'9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025'),
 'D':('deploy/maintenance/app_r2_observe02/datastore_candidate_contract.py',38383,'253f234f2ba68dea6929e16128d8dcb48021cda7bf71ffd004ecb8e709c575ee'),
 'T':('deploy/maintenance/app_r2_observe02/runtime_image_archive_transport.py',35148,'20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2'),
 'IO':('scripts/security_age132_tool/source_io.py',8831,'b2e6159d62ded26c81c5a72ad9dbda73f6029719451ff2b27d7edeefeae7b568'),
}

def need(value,code):
 if not value:raise RuntimeError(code)
def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def hex64(value):return type(value)is str and re.fullmatch('[a-f0-9]{64}',value)is not None

# Purpose-specific original normal-operation clock. Existing SourceIO owns FDs.
NORMAL_OPERATION_SECONDS=6000
NORMAL_PROBE_FINALITY_RESERVE=600
NORMAL_ROLES=frozenset(('capture2-f79','native/coin-estimator','native/estimator-snapshot-sender','native/market-fact-receiver','native/market-store-adapter','web/estimator-snapshot-receiver','web/market-capture-account1','web/market-capture-external','web/market-fact-sync-worker','web/market-processor'))
def clock_identity(s):
 return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def clock_document(raw):
 def pairs(rows):
  out={}
  for k,v in rows:need(k not in out,'operation_duplicate');out[k]=v
  return out
 need(type(raw)is bytes and 0<len(raw)<=4096,'operation_bound')
 return json.loads(raw,object_pairs_hook=pairs,parse_float=lambda _:need(False,'operation_nonfinite'),parse_constant=lambda _:need(False,'operation_nonfinite'))
def clock_pin(pin,maximum):
 need(type(pin)is dict and set(pin)=={'bytes','sha256','full9'}and type(pin['bytes'])is int
  and 0<pin['bytes']<=maximum and hex64(pin['sha256'])and type(pin['full9'])is list
  and len(pin['full9'])==9 and all(type(n)is int for n in pin['full9']),'operation_pin')
 return(pin['bytes'],pin['sha256'])
def normal_boot_id():
 # A boot/domain identifier only: never a job start or permission operand.
 path=Path('/proc/sys/kernel/random/boot_id');fd=None;before=None;raw=None;errors=[]
 try:
  before=clock_identity(path.lstat());fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
  fl=(fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL))
  need(clock_identity(os.fstat(fd))==clock_identity(path.lstat())==before and path.resolve(strict=True)==path,'operation_boot_identity')
  raw=os.pread(fd,65,0)
  need(re.fullmatch(rb'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\n',raw)is not None
   and clock_identity(os.fstat(fd))==clock_identity(path.lstat())==before
   and fl==(fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL))and os.lseek(fd,0,os.SEEK_CUR)==0,'operation_boot_identity')
 except BaseException as error:errors.append(error)
 finally:
  if fd is not None:
   try:
    os.close(fd)
    try:os.fstat(fd)
    except OSError as error:need(error.errno==errno.EBADF,'operation_physical_close')
    else:need(False,'operation_physical_close')
    need(before is None or clock_identity(path.lstat())==before,'operation_boot_identity')
   except BaseException as error:errors.append(error)
 if errors:raise BaseExceptionGroup('operation boot identity and finalizer',errors)
 return raw.decode('ascii').strip()
def write_operation_file(path,raw,io,deadline_ns):
 need(path.name in('normal-operation-clock.json','market_sqlite_component_probe.py','source_io.py')and type(raw)is bytes
  and 0<len(raw)<=131072 and type(deadline_ns)is int,'operation_bound')
 def check():need(time.monotonic_ns()<deadline_ns,'operation_deadline')
 check();scope=io.Scope(60);ref=None;primary=None
 try:
  w=scope.create(path,131072)
  for offset in range(0,len(raw),65536):check();w.append(raw[offset:offset+65536]);check()
  ref=w.seal();r=scope.open(path,(ref['bytes'],ref['sha256']),131072);before=list(r.before)
  w.close();need(r.hash()==ref['sha256'],'operation_changed');r.close();check()
 except BaseException as error:primary=error
 try:scope.finish()
 except BaseException as cleanup:
  if primary is not None:raise BaseExceptionGroup('operation write and Source finality',[primary,cleanup])
  raise
 if primary is not None:raise primary
 check();return{**{k:ref[k]for k in('bytes','sha256')},'full9':before}
class ClockSource:
 def __init__(self,path,pin,io,io_pin=None,*,parent=None):
  self.path=path;self.io=io;self.parent=parent;self.scope=io.Scope(6000);self.attempted=False;self.closed=False;self.failure=None
  self.row=None;self.io_row=None;self.before=None;self.pin=pin
  try:
   need(path.name==('market_sqlite_component_probe.py'if parent is None else'normal-operation-clock.json'),'operation_file')
   if parent is None:
    need(type(io)is ModuleType and io.__file__==str(path.parent/'source_io.py'),'operation_source')
    need(clock_pin(io_pin,131072)==COHORT['IO'][1:],'operation_source')
    self.io_row=self.scope.open(path.parent/'source_io.py',COHORT['IO'][1:],131072)
    need(list(self.io_row.before)==io_pin['full9'],'operation_changed')
   else:need(type(parent)is ClockSource and parent.io is io,'operation_source');parent.check()
   self.row=self.scope.open(path,clock_pin(pin,4096 if parent is not None else 131072),131072)
   self.before=self.row.before;need(list(self.before)==pin['full9']and self.before[2]==0o100600,'operation_changed')
   self.raw=read_owned(self.row,131072,self.scope.check)
   self.origin=(path,self.row,self.io_row,self.scope,self.io,parent,self.before,encoded(pin));self.check()
  except BaseException as primary:
   try:self.close()
   except BaseException as cleanup:raise BaseExceptionGroup('operation admission and Source finality',[primary,cleanup])
   raise
 def metadata(self):
  need((self.path,self.row,self.io_row,self.scope,self.io,self.parent,self.before,encoded(self.pin))==self.origin,'operation_origin')
  self.scope.check()
  if self.parent is not None:self.parent.check()
 def check(self):
  try:need(self.attempted is False and self.closed is False and self.failure is None,'operation_retired');self.metadata()
  except BaseException as error:self.failure=error;self.scope.note(error);raise
 def named(self):
  need((self.path,self.row,self.io_row,self.scope,self.io,self.parent,self.before,encoded(self.pin))==self.origin,'operation_origin')
  for row in self.scope.rows:row.named()
 def close(self):
  if self.attempted:
   if self.failure is not None:raise self.failure
   need(self.closed,'operation_physical_close');return
  self.attempted=True
  try:self.scope.finish();self.closed=True
  except BaseException as error:self.failure=error;raise
class NormalOperationClock(ClockSource):
 def __init__(self,path,pin,context,source):
  super().__init__(path,pin,source.io,parent=source);self.source=source
  try:
   need(type(context)is dict and set(context)=={'root','release_commit','run_id','run_attempt','job','role','driver_sha256','boot_id','clock_source_sha256','clock_source_full9','io_source_sha256','io_source_full9'}
    and type(context['root'])is str and Path(context['root']).is_absolute()and Path(context['root'])==path.parent
    and type(context['release_commit'])is str and re.fullmatch('[a-f0-9]{40}',context['release_commit'])is not None
    and all(type(context[k])is str and re.fullmatch('[1-9][0-9]{0,19}',context[k])is not None for k in('run_id','run_attempt'))
    and context['job']=='candidate'and type(context['role'])is str and context['role']in NORMAL_ROLES
    and hex64(context['driver_sha256'])and re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',context['boot_id'])is not None
    and context['clock_source_sha256']==source.pin['sha256']and context['clock_source_full9']==list(source.before)
    and context['io_source_sha256']==COHORT['IO'][2]and context['io_source_full9']==list(source.io_row.before),'operation_context')
   value=clock_document(self.raw)
   need(type(value)is dict and set(value)=={'schema','context','started_ns','deadline_ns','hard_seconds','scheduler_job_start_proven','security_accepted'}
    and value['schema']=='normal_market_operation_clock/1'and value['context']==context and type(value['hard_seconds'])is int
    and value['hard_seconds']==NORMAL_OPERATION_SECONDS and value['scheduler_job_start_proven']is False and value['security_accepted']is False,'operation_record')
   self.started=value['started_ns'];self.end=value['deadline_ns'];self.context=context;self.frozen_context=encoded(context)
   need(type(self.started)is int and type(self.end)is int and self.started>0 and self.end==self.started+NORMAL_OPERATION_SECONDS*10**9,'operation_original_clock')
   self.original=(self.started,self.end);self.check()
  except BaseException as primary:
   try:self.close()
   except BaseException as cleanup:raise BaseExceptionGroup('operation clock and Source finality',[primary,cleanup])
   raise
 def check(self):
  # ClockSource construction deliberately checks only its original file role.
  if not hasattr(self,'original'):return ClockSource.check(self)
  try:
   ClockSource.check(self);need((self.started,self.end)==self.original and encoded(self.context)==self.frozen_context
    and self.started<=time.monotonic_ns()<self.end,'operation_deadline')
  except BaseException as error:self.failure=error;raise
 def remaining(self):self.check();return(self.end-time.monotonic_ns())/10**9
 def probe_budget(self):
  self.check();now=time.monotonic_ns();healthy=now+1500*10**9;hard=now+1800*10**9
  need(hard+NORMAL_PROBE_FINALITY_RESERVE*10**9<=self.end,'original_job_budget_missing_or_insufficient')
  return{'normal_deadline':self.end/10**9,'probe_started':now/10**9,'healthy_end':healthy/10**9,'hard_end':hard/10**9,
   'normal_deadline_ns':self.end,'probe_started_ns':now,'healthy_end_ns':healthy,'hard_end_ns':hard,
   'surrounding_finality_seconds':NORMAL_PROBE_FINALITY_RESERVE}
 def final_check(self):
  try:
   need(self.failure is None and self.source.failure is None and self.attempted is True and self.closed is True and self.scope.finished is True
    and self.source.attempted is True and self.source.closed is True and self.source.scope.finished is True
    and all(r.attempted is True and r.closed is True and r.physical_EBADF is True and r.fd is None for owner in(self,self.source)for r in owner.scope.rows)
    and(self.started,self.end)==self.original and encoded(self.context)==self.frozen_context
    and self.started<=time.monotonic_ns()<self.end,'operation_deadline');self.named();self.source.named()
  except BaseException as error:self.failure=error;raise
def create_normal_operation(root,context,started,source):
 need(type(started)is int and 0<started<=time.monotonic_ns()<started+NORMAL_OPERATION_SECONDS*10**9,'operation_bootstrap_missing')
 value={'schema':'normal_market_operation_clock/1','context':context,'started_ns':started,'deadline_ns':started+NORMAL_OPERATION_SECONDS*10**9,
  'hard_seconds':NORMAL_OPERATION_SECONDS,'scheduler_job_start_proven':False,'security_accepted':False}
 return write_operation_file(root/'normal-operation-clock.json',encoded(value),source.io,value['deadline_ns'])

def budget(original_normal_deadline,*,now=None):
 """Check an original producer operand; never create/extend the normal clock."""
 now=time.monotonic()if now is None else now
 need(type(now)in(int,float)and math.isfinite(now)and type(original_normal_deadline)in(int,float)
  and math.isfinite(original_normal_deadline)and original_normal_deadline-now>=HARD_SECONDS,'original_job_budget_missing_or_insufficient')
 return{'normal_deadline':original_normal_deadline,'probe_started':now,'healthy_end':now+HEALTHY_SECONDS,'hard_end':now+HARD_SECONDS}

def read_owned(row,maximum,guard):
 """SourceIO's original FD remains at offset0, including after parse failures."""
 need(type(maximum)is int and 0<maximum<=MAX_INVENTORY and row.before is not None and 0<row.before[6]<=maximum,'probe_input_bound')
 chunks=[];offset=0
 while offset<row.before[6]:
  guard();chunk=row.read(min(65536,row.before[6]-offset),offset);need(chunk,'probe_input_short');chunks.append(chunk);offset+=len(chunk)
 guard();return b''.join(chunks)

def private_modules(io,scope,source_directory,guard):
 """Exact existing files from a sealed new controller commit, no .local API."""
 need(isinstance(source_directory,Path)and source_directory.is_absolute(),'probe_source_directory')
 modules={};owners={}
 for role,(name,size,pin)in COHORT.items():
  guard();row=scope.open(source_directory/name,(size,pin),131072);owners[role]=row
  raw=read_owned(row,131072,guard);module=ModuleType('_market_sqlite_component_'+role)
  module.__file__=str(source_directory/name);exec(compile(raw,module.__file__,'exec'),module.__dict__);modules[role]=module
 old=modules['old'];expected={Path(COHORT[k][0]).name:COHORT[k][2]for k in('C','P','M','L','D','T')}
 need({n:p[1]for n,p in old.HELPERS.items()}==expected and old.DOCKER==['/usr/bin/docker','--host=unix:///var/run/docker.sock'],'probe_legacy_cohort')
 return modules,owners

def strict_sealed(io,scope,root,stack_owner,helper_pins,guard):
 """Only the fixed six-argument request operands of original old.sealed."""
 def sealed(stack,path,pin,maximum):
  need(stack is stack_owner and isinstance(path,Path)and path.parent==root/'source'
   and path.name in helper_pins and pin==helper_pins[path.name][1]and maximum==131072,'probe_sealed_operands')
  expected=helper_pins[path.name];need(path.stat().st_mode&0o777==0o600,'probe_copied_source_mode')
  guard();row=scope.open(path,expected,maximum);stack.callback(row.close)
  raw=read_owned(row,maximum,guard)
  def check():guard();row.check()
  return raw,check
 return sealed

def strict_write(io,scope,root,guard):
 """The E profile only requests exclusive600 bounded public JSON outputs."""
 allowed={'flat-export-created.json','flat-export-receipt.json','fresh-whole-image-acl-inventory.json',
  *('flat-export-cli-'+stage+'.json'for stage in('create','preinspect','export','postinspect'))}
 def write(path,raw,mode=0o600):
  need(isinstance(path,Path)and path.parent==root/'reports'and path.name in allowed and mode==0o600
   and type(raw)is bytes and 0<len(raw)<=MAX_INVENTORY,'probe_publication_operands')
  guard();ref=io.publish(scope,path,raw,MAX_INVENTORY);guard();return ref
 return write

def context(modules,source_owners,io,scope,root,stack,original_guard,original_budget):
 """The genuine frozen constructor and its unchanged hosted UID1001 predicate.

 This helper neither creates a fake Context nor modifies an existing module.
 Its arguments are fresh private instances admitted by private_modules. It is
 consumed only under the original normal-operation clock and FD owners.
 """
 old=modules['old'];old.ci(root)
 need(type(stack)is ExitStack and all(type(original_budget[k])is int for k in('probe_started_ns','hard_end_ns','healthy_end_ns','normal_deadline_ns'))
  and original_budget['hard_end_ns']-original_budget['probe_started_ns']==HARD_SECONDS*10**9
  and original_budget['healthy_end_ns']-original_budget['probe_started_ns']==HEALTHY_SECONDS*10**9
  and original_budget['normal_deadline_ns']>=original_budget['hard_end_ns']+NORMAL_PROBE_FINALITY_RESERVE*10**9,'probe_context_budget')
 def guard():
  original_guard();scope.check();need(time.monotonic_ns()<original_budget['hard_end_ns'],'probe_deadline')
 for name in('source','reports','docker-config'):(root/name).mkdir(mode=0o700)
 helpers={Path(COHORT[k][0]).name:(COHORT[k][1],COHORT[k][2])for k in('C','P','M','L','D','T')}
 for role in('C','P','M','L','D','T'):
  name=Path(COHORT[role][0]).name;raw=read_owned(source_owners[role],131072,guard)
  io.publish(scope,root/'source'/name,raw,131072)
 old.sealed=strict_sealed(io,scope,root,stack,helpers,guard)
 ctx=old.Controller(root,stack)
 ctx.C.PROOF_KEEPER=ctx.C.ProofKeeper(root)
 ctx.C.write=strict_write(io,scope,root,guard)
 ctx.end=min(ctx.end,original_budget['healthy_end']);fixed_end=ctx.end;original_check=ctx.check
 def check():
  guard();need(ctx.end==fixed_end and(ctx.cleanup_end is None or ctx.cleanup_end<=original_budget['hard_end']),'probe_clock_changed')
  original_check();ctx.C.PROOF_KEEPER.check();guard()
 ctx.check=check
 return ctx

def whole_inventory(modules,ctx,image,control,sbom,guard):
 """Only original E creates/inspects/exports; no LOAD/pull/start/run/exec path."""
 old,E,A,I=(modules[k]for k in('old','E','A','I'))
 name='app-ci-flat-acl-'+old.owner();guard();ctx.check()
 need(ctx.created==[]and ctx.docker(['container','ls','--all','--no-trunc','--quiet','--filter','name=^/'+name+'$'],maximum=256)==b'','probe_preexisting_export_container')
 return E.whole_image_inventory(ctx,old,image,control,sbom,I=I,T=ctx.T,A=A,adapter_sha256=COHORT['A'][2])

class PreadView:
 """Tiny seek view for the old pure D/T/L API, original SourceIO offset stays0."""
 def __init__(self,row,guard,end):self.row,self.guard,self.end,self.position=row,guard,end,0
 def check(self):self.guard();self.row.check();need(time.monotonic()<self.end,'probe_archive_deadline')
 def tell(self):self.check();return self.position
 def seek(self,offset,whence=os.SEEK_SET):
  self.check();need(type(offset)is int and whence in(os.SEEK_SET,os.SEEK_CUR,os.SEEK_END),'probe_archive_seek')
  target=offset+(0 if whence==os.SEEK_SET else self.position if whence==os.SEEK_CUR else self.row.before[6])
  need(0<=target<=self.row.before[6],'probe_archive_seek');self.position=target;self.check();return target
 def read(self,size):
  self.check();need(type(size)is int and 0<=size<=MAX_INVENTORY and self.position+size<=self.row.before[6]+1,'probe_archive_read')
  out=[];remaining=size
  while remaining:
   self.check();raw=self.row.read(min(65536,remaining),min(self.position,self.row.before[6]));out.append(raw);self.position+=len(raw);remaining-=len(raw)
   if not raw:break
  self.check();return b''.join(out)

def physical_archive(modules,row,image,layers,inspect,guard,end):
 """Original D/T/L admission over the same retained layered archive bytes."""
 D,T,L=(modules[k]for k in('D','T','L'))
 need(type(image)is str and re.fullmatch('sha256:[a-f0-9]{64}',image)and type(layers)is list and 0<len(layers)<=128
  and all(type(v)is str and re.fullmatch('sha256:[a-f0-9]{64}',v)for v in layers),'probe_image_identity')
 need(inspect['Id']==image and inspect['RootFS']=={'Type':'layers','Layers':layers},'probe_image_identity')
 data={'spec':{'archive':{'uncompressed_bytes':row.before[6]},'config_id':image,'layers':layers,'profile':'Market-sqlite119586-source-only'},
  'image':{k:inspect.get(k,''if k!='Config'else{})for k in('Config','Comment','Author','DockerVersion')}}
 value=D.validate_archive(PreadView(row,guard,end),row.before[6],end,data,T,L);guard()
 need(value['config_id']==image and value['rootfs_layers']==layers and value['archive_bytes']==row.before[6]
  and value['archive_sha256']==row.pin[1]and value['physical_graph_closed']is True and value['repo_tags_empty']is True
  and value['security_accepted']is False,'probe_physical_archive_join')
 return value

def component_report(inventory,*,image,source_commit,layers,scan,link_evidence=None):
 """Inventory bindings only. No Source/build/link evidence can be guessed."""
 need(link_evidence is None,'probe_source_link_evidence_not_implemented')
 need(type(inventory)is dict and inventory.get('schema')=='image_acl_archive_inventory/1'and inventory.get('image_id')==image
  and inventory.get('source_commit')==source_commit and inventory.get('security_accepted')is False,'probe_inventory_join')
 need(scan.get('Metadata',{}).get('ImageID')==image and type(scan.get('Results'))is list,'probe_scan_join')
 findings=[]
 for result in scan['Results']:
  need(type(result)is dict and(result.get('Vulnerabilities')is None or type(result['Vulnerabilities'])is list),'probe_scan_shape')
  for row in result.get('Vulnerabilities')or[]:
   need(type(row)is dict,'probe_scan_shape')
   if row.get('VulnerabilityID')=='CVE-2026-119586'and row.get('PkgName')=='libsqlite3-0':
    need(type(row.get('InstalledVersion'))is str and 0<len(row['InstalledVersion'])<=256,'probe_scan_package');findings.append(row)
 need(len(findings)<=1,'probe_duplicate_scan_target')
 rows=inventory.get('inventory',{}).get('elf_files');need(type(rows)is list and 0<len(rows)<=8192,'probe_inventory_shape')
 paths=set();target=[];others=[]
 for row in rows:
  need(type(row)is dict and type(row.get('path'))is str and row['path'].startswith('/')and row['path']not in paths
   and hex64(row.get('sha256'))and type(row.get('bytes'))is int and row['bytes']>0 and type(row.get('package_owners'))is list,'probe_ELF_identity');paths.add(row['path'])
  owners=[p for p in row['package_owners']if p.get('package')=='libsqlite3-0']
  if owners:
   need(len(owners)==1,'probe_duplicate_package_owner');owner=owners[0];sbom=owner.get('sbom')
   need(owner.get('architecture')=='amd64'and owner.get('source_package')=='sqlite3'and owner.get('sbom_source_binding_matches')is True
    and row.get('source_binding_status')=='BOUND_DPKG_AND_SEALED_SBOM'and type(sbom)is dict
    and all(owner.get(k)==sbom.get(k)for k in('package','installed_version','architecture','source_package','source_version'))
    and sbom.get('layer_diffid')in layers,'probe_package_source_join')
   if findings:need(owner['installed_version']==findings[0]['InstalledVersion'],'probe_scan_package_join')
   target.append({'path':row['path'],'bytes':row['bytes'],'sha256':row['sha256'],'package_owner':owner,
    'symlink_aliases':row.get('symlink_aliases',[]),'archive_payload_member':row.get('archive_payload_member'),
    'elf_parse_status':row.get('elf_parse_status'),'component_status':'UNASSESSED'})
  else:others.append({'path':row['path'],'bytes':row['bytes'],'sha256':row['sha256'],'component_status':'UNASSESSED'})
 value={'schema':'Market_sqlite_package_component_inventory/1','status':'UNASSESSED','advisory':'CVE-2026-119586',
  'scope':'exact scanned libsqlite3-0 payload/core-library versus affected CLI shell','image_id':image,'source_commit':source_commit,
  'scanner_target_observed':bool(findings),'scanner_target_count':len(findings),'package_ELF_candidates':target,
  'other_ELF_candidates_unassessed':others,'inventory_ELF_count':len(rows),'source_link_join':'MISSING',
  'generated_source_object_link_strip_deb_to_image_DSO_join_complete':False,'whole_image_component_absence_proven':False,
  'raw_scanner_findings_modified':False,'runtime_install_allowed':False,'security_accepted':False}
 need(len(encoded(value))<=MAX_COMPONENT,'probe_component_report_bound');return value

def cleanup(old,ctx,expected_name,image,guard,hard_end):
 """No pre-existing actor is ever adopted. Keep original owner cleanup logic."""
 guard();need(time.monotonic()+120<hard_end and ctx.created in([] ,[(expected_name,image)]),'probe_cleanup_budget_or_owner')
 failures=old.cleanup(ctx);guard()
 names=ctx.docker(['container','ls','--all','--no-trunc','--quiet','--filter','name=^/'+expected_name+'$'],maximum=256)
 need(not failures and names==b'','probe_owned_cleanup_unproven');guard()
 return{'original_owned_cleanup_completed':True,'fresh_exact_name_absent':True}

class OriginalInput:
 """Pread the existing normal owner's original FD; never reopen its payload."""
 def __init__(self,owner,path,maximum,guard):
  self.admitted=False;self.failure=None
  self.owner=owner;self.path=path;self.guard=guard;self.fd=owner.fd;self.before=owner.before
  self.pin=(owner.binding['bytes'],owner.binding['sha256'])
  need(owner.path==path and type(maximum)is int and 0<self.before[6]<=maximum
   and self.pin[0]==self.before[6]and hex64(self.pin[1]),'probe_input_owner')
  self.flags=(fcntl.fcntl(self.fd,fcntl.F_GETFD),fcntl.fcntl(self.fd,fcntl.F_GETFL));self.offset=os.lseek(self.fd,0,os.SEEK_CUR)
  need(0<=self.offset<=self.before[6],'probe_input_offset')
  self.origin=(owner,path,self.fd,self.before,self.pin,self.flags,self.offset);self.admitted=True;self.check()
 def check(self):
  try:
   need(getattr(self,'admitted',False)and self.failure is None,'probe_input_failed');self.guard()
   need((self.owner,self.path,self.fd,self.before,self.pin,self.flags,self.offset)==self.origin
    and self.owner.fd==self.fd and self.owner.before==self.before
    and(self.owner.binding['bytes'],self.owner.binding['sha256'])==self.pin,'probe_input_owner')
   self.owner.check();need(clock_identity(os.fstat(self.fd))==self.before
    and(self.flags==(fcntl.fcntl(self.fd,fcntl.F_GETFD),fcntl.fcntl(self.fd,fcntl.F_GETFL)))
    and os.lseek(self.fd,0,os.SEEK_CUR)==self.offset,'probe_input_changed')
  except BaseException as error:self.failure=error;raise
 def read(self,n,offset):
  need(type(n)is int and 0<n<=65536 and type(offset)is int and 0<=offset<=self.before[6],'probe_input_bound')
  errors=[];raw=None
  try:self.check();raw=os.pread(self.fd,n,offset)
  except BaseException as error:errors.append(error)
  try:self.check()
  except BaseException as error:errors.append(error)
  if errors:raise BaseExceptionGroup('original input read and postguard',errors)
  return raw

def collect(normal_clock,root=None,source_directory=None,image=None,source_commit=None,inspect=None,scan_reader=None,
 sbom_reader=None,archive_reader=None,original_guard=None,*,normal_deadline=None):
 """Only a fresh qualified operation can collect initial UNASSESSED metadata."""
 if type(normal_clock)is not NormalOperationClock:
  return{'status':'UNQUALIFIED','missing':['fresh authenticated normal operation clock and finalization caller'],
   'collection_performed':False,'original_scope_readers_closed':False,'security_accepted':False}
 need(normal_deadline is None,'legacy_deadline_not_authority');normal_clock.check()
 need(isinstance(root,Path)and root.is_absolute()and str(root)==normal_clock.context['root']
  and source_commit==normal_clock.context['release_commit']and callable(original_guard)
  and isinstance(source_directory,Path)and source_directory==root/'controller-source'
  and type(image)is str and re.fullmatch('sha256:[a-f0-9]{64}',image)is not None,'probe_normal_owner')
 need(type(inspect)is dict and inspect.get('Id')==image and type(inspect.get('RootFS'))is dict
  and inspect['RootFS'].get('Type')=='layers'and type(inspect['RootFS'].get('Layers'))is list,'probe_image_identity')
 budget=normal_clock.probe_budget();io=normal_clock.source.io;scope=io.Scope(HARD_SECONDS);stack=ExitStack()
 ctx=None;keeper=None;value=None;errors=[];probe_root=None;borrowed=[]
 def guard():normal_clock.check();original_guard();need(time.monotonic_ns()<budget['hard_end_ns'],'probe_deadline')
 def inputs_guard():guard();scope.check()
 try:
  def borrow(owner,path,maximum):
   item=OriginalInput.__new__(OriginalInput);borrowed.append(item);OriginalInput.__init__(item,owner,path,maximum,guard);return item
  guard();scan=borrow(scan_reader,root/'reports/vulnerabilities.json',MAX_INVENTORY)
  sbom=borrow(sbom_reader,root/'reports/sbom.cdx.json',MAX_INVENTORY)
  archive=borrow(archive_reader,root/'candidate-image.tar',MAX_EXPORT)
  def all_inputs():inputs_guard();scan.check();sbom.check();archive.check()
  modules,owners=private_modules(io,scope,source_directory,all_inputs)
  scan_value=modules['old'].document(read_owned(scan,MAX_INVENTORY,all_inputs))
  sbom_raw=read_owned(sbom,MAX_INVENTORY,all_inputs)
  physical=physical_archive(modules,archive,image,inspect['RootFS']['Layers'],inspect,all_inputs,budget['healthy_end'])
  # The original constructor requires this exact newly owned sibling prefix.
  # Its predicate, UID/root and __file__ semantics are never replaced.
  parent=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
  need(root.parent==parent,'probe_root_parent');guard()
  probe_root=Path(tempfile.mkdtemp(prefix='security-app-ci-smoke-',dir=parent));os.chmod(probe_root,0o700)
  ctx=context(modules,owners,io,scope,probe_root,stack,all_inputs,budget);keeper=ctx.C.PROOF_KEEPER
  control={'image_id':image,'source_commit':source_commit,'sbom_sha256':sbom.pin[1]}
  inventory=whole_inventory(modules,ctx,image,control,sbom_raw,all_inputs)
  value=component_report(inventory,image=image,source_commit=source_commit,layers=inspect['RootFS']['Layers'],scan=scan_value)
  value.update(collection_performed=True,physical_archive={k:physical[k]for k in('archive_bytes','archive_sha256','config_id','rootfs_layers','physical_graph_closed','repo_tags_empty','security_accepted')},
   normal_operation={'schema':'normal_market_operation_clock/1','started_ns':normal_clock.started,'deadline_ns':normal_clock.end,
    'hard_seconds':NORMAL_OPERATION_SECONDS,'scheduler_job_start_proven':False,'probe_hard_seconds':HARD_SECONDS,
    'probe_healthy_seconds':HEALTHY_SECONDS,'probe_cleanup_reserve_seconds':CLEANUP_RESERVE,'surrounding_finality_seconds':NORMAL_PROBE_FINALITY_RESERVE})
  all_inputs()
 except BaseException as error:errors.append(error)
 finally:
  if ctx is not None:
   try:
    name='app-ci-flat-acl-'+modules['old'].owner()
    proof=cleanup(modules['old'],ctx,name,image,guard,budget['hard_end'])
    if value is not None:value['owned_cleanup']=proof
   except BaseException as error:errors.append(error)
  if keeper is not None:
   try:keeper.close();guard()
   except BaseException as error:errors.append(error)
  try:stack.close()
  except BaseException as error:errors.append(error)
  try:scope.finish();guard()
  except BaseException as error:errors.append(error)
  for row in reversed(borrowed):
   try:row.check()
   except BaseException as error:errors.append(error)
 if errors:
  if len(errors)==1:raise errors[0]
  raise BaseExceptionGroup('component collection and physical finalizers',errors)
 need(value is not None and scope.finished and all(row.closed and row.physical_EBADF for row in scope.rows),'probe_physical_finality')
 value.update(original_probe_Source_readers_physically_closed=len(scope.rows),original_normal_input_readers_retained=True)
 guard();need(len(encoded(value))<=MAX_COMPONENT,'probe_component_report_bound');return value
