"""UNARMED never-started public-image export proposal; no action on import.

The authenticated outer controller supplies its admitted image/control, frozen
pure inventory module/adapter, and private C.ProofKeeper instance. No capability,
image mode, native probe, original smoke assertion or raw finding is changed.
"""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json, os, re, selectors, stat, subprocess, sys, time

EXECUTION_ARMED = True
MAX_EXPORT=2*1024**3
MAX_STDERR=8192
MAX_REPORT=4*1024**2
MAX_INVENTORY_SECONDS=600
STAGES=frozenset(('create','preinspect','export','postinspect'))
ZERO_TIME='0001-01-01T00:00:00Z'
PURE_INVENTORY_SHA256='e8171fb6d28a5b3e938e0136dab230c13a9e864f66aad4c2727878e22a3ea73f'
PUBLIC_FAILURE_CODES=frozenset(('flat_export_unarmed','flat_export_contract','flat_export_metadata','flat_export_changed',
 'flat_export_bound','flat_export_write','flat_export_readback','flat_export_not_frozen','flat_export_close',
 'flat_export_stage','flat_export_timeout','flat_export_stdout_bound','flat_export_stderr_bound',
 'flat_export_client','flat_export_pipe_close','flat_export_receipt','flat_export_container','flat_export_scope'))

def need(ok,code):
 if not ok:raise RuntimeError(code)
def identity(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def encode(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def hex64(value):return type(value)is str and re.fullmatch('[a-f0-9]{64}',value)is not None
def image_id(value):return type(value)is str and re.fullmatch('sha256:[a-f0-9]{64}',value)is not None
def document(raw):
 need(type(raw)is bytes and 0<len(raw)<=16384,'flat_export_receipt')
 def pairs(rows):
  value={}
  for key,item in rows:need(key not in value,'flat_export_receipt');value[key]=item
  return value
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False,'flat_export_receipt'))

class ExportFile:
 """One fixed EXCL output, registered before any payload, held by outer keeper.

Only our successful writes change the expected full9. Independent readback must
match the incremental received-byte hash before any collector FD is closed.
The private RW FD is CLOEXEC and never inherited by the Docker client.
"""
 def __init__(self,C,keeper,root):
  self.C=C;self.path=Path(root)/'whole-image-export.tar';self.fd=None;self.parents=None
  self.before=None;self.expected_sha256=None;self.count=0;self.digest=sha256();self.frozen=False
  need(C.PROOF_KEEPER is keeper and keeper.root==Path(root)and not keeper.closed,'flat_export_contract')
  keeper._admit(self.path)
  try:
   self.parents=C._ProofParents(self.path)
   self.fd=os.open(self.path.name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=self.parents.fd)
   self.before=identity(os.fstat(self.fd));self.created=self.before[:6]
   need(self.before[2:7]==(0o100600,os.geteuid(),os.getegid(),1,0),'flat_export_metadata')
   self.check();keeper.records.append(self)
  except BaseException:
   if self.fd is not None:os.close(self.fd);self.fd=None
   if self.parents is not None:self.parents.close()
   raise
 def named(self):
  self.parents.named()
  need(self.path.resolve(strict=True)==self.path and identity(self.path.lstat())==self.before,'flat_export_changed')
 def check(self):
  self.parents.check();self.named()
  need(identity(os.fstat(self.fd))==self.before,'flat_export_changed')
 def write(self,raw,check):
  need(type(raw)is bytes and 0<len(raw)<=65536 and not self.frozen,'flat_export_write')
  need(self.count+len(raw)<=MAX_EXPORT,'flat_export_bound');view=memoryview(raw)
  while view:
   check();self.check();need(os.lseek(self.fd,0,os.SEEK_CUR)==self.count,'flat_export_changed')
   n=os.write(self.fd,view);need(0<n<=len(view),'flat_export_write')
   self.count+=n;self.digest.update(view[:n]);view=view[n:]
   after=identity(os.fstat(self.fd))
   need(after[:6]==self.created and after[6]==self.count and identity(self.path.lstat())==after,'flat_export_changed')
   self.before=after;self.check();check()
 def freeze(self,check,end):
  need(not self.frozen and 0<self.count<=MAX_EXPORT and self.count%512==0,'flat_export_bound')
  check();self.check();os.fsync(self.fd);self.check();snapshot=self.before;digest=sha256();offset=0
  while offset<self.count:
   need(time.monotonic()<end,'flat_export_timeout');check();self.check()
   raw=os.pread(self.fd,min(65536,self.count-offset),offset);need(raw,'flat_export_readback')
   offset+=len(raw);digest.update(raw);self.check()
  need(os.pread(self.fd,1,self.count)==b''and digest.hexdigest()==self.digest.hexdigest()
   and self.before==snapshot,'flat_export_readback')
  self.expected_sha256=self.digest.hexdigest();self.frozen=True;self.check();check();return self.binding()
 def binding(self):
  need(self.frozen,'flat_export_not_frozen')
  return{'bytes':self.count,'sha256':self.expected_sha256,'full9':list(self.before)}
 @contextmanager
 def borrow(self,check):
  need(self.frozen,'flat_export_not_frozen');check();self.check()
  try:
   with os.fdopen(os.dup(self.fd),'rb')as stream:
    stream.seek(0);yield stream
  finally:self.check();check()
 def close(self):
  errors=[]
  if self.fd is not None:
   try:self.check()
   except BaseException as error:errors.append(error)
   try:os.close(self.fd)
   except BaseException as error:errors.append(error)
   self.fd=None
  try:self.parents.close()
  except BaseException as error:errors.append(error)
  try:self.named()
  except BaseException as error:errors.append(error)
  need(not errors,'flat_export_close')

def create_args(old,name,image):
 return['create','--name',name,'--label',old.LABEL+'='+old.owner(),'--pull=never',
  '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
  '--user=0:0','--memory=512m','--memory-swap=512m','--cpus=1','--pids-limit=64',
  '--restart=no','--ulimit','core=0','--entrypoint','/bin/true',image,'--inventory-never-started']

def inspect_template(old):
 return '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"config_image":{{json .Config.Image}},"owner":{{json (index .Config.Labels "'+old.LABEL+'")}},"user":{{json .Config.User}},"entrypoint":{{json .Config.Entrypoint}},"cmd":{{json .Config.Cmd}},"volumes":{{json (index .Config "Volumes")}},"readonly":{{json .HostConfig.ReadonlyRootfs}},"privileged":{{json .HostConfig.Privileged}},"network":{{json .HostConfig.NetworkMode}},"networks":{{json .NetworkSettings.Networks}},"caps_add":{{json .HostConfig.CapAdd}},"caps_drop":{{json .HostConfig.CapDrop}},"security":{{json .HostConfig.SecurityOpt}},"memory":{{json .HostConfig.Memory}},"memory_swap":{{json .HostConfig.MemorySwap}},"nano_cpus":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}},"tmpfs":{{json (index .HostConfig "Tmpfs")}},"mounts":{{json .Mounts}},"binds":{{json (index .HostConfig "Binds")}},"host_mounts":{{json (index .HostConfig "Mounts")}},"restart_policy":{{json .HostConfig.RestartPolicy.Name}},"auto_remove":{{json .HostConfig.AutoRemove}},"status":{{json .State.Status}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"restarting":{{json .State.Restarting}},"dead":{{json .State.Dead}},"pid":{{json .State.Pid}},"exit_code":{{json .State.ExitCode}},"oom_killed":{{json .State.OOMKilled}},"started_at":{{json .State.StartedAt}},"finished_at":{{json .State.FinishedAt}},"restart_count":{{json .RestartCount}}}'

def admit_container(value,old,name,image,cid):
 expected={'id':cid,'name':'/'+name,'image':image,'config_image':image,'owner':old.owner(),
  'user':'0:0','entrypoint':['/bin/true'],'cmd':['--inventory-never-started'],'readonly':True,
  'privileged':False,'network':'none','caps_drop':['ALL'],'security':['no-new-privileges'],
  'mounts':[],'restart_policy':'no','auto_remove':False,'status':'created','running':False,
  'paused':False,'restarting':False,'dead':False,'oom_killed':False,'started_at':ZERO_TIME,'finished_at':ZERO_TIME}
 numbers=(('memory',512*1024**2),('memory_swap',512*1024**2),('nano_cpus',10**9),('pids',64),('pid',0),('exit_code',0),('restart_count',0))
 need(type(value)is dict and set(value)==set(expected)|{key for key,_ in numbers}|{'networks','caps_add','volumes','tmpfs','binds','host_mounts'}
  and all(type(value.get(k))is type(v)and value[k]==v for k,v in expected.items()),'flat_export_container')
 for key,number in numbers:
  need(type(value.get(key))is int and value[key]==number,'flat_export_container')
 need(type(value.get('networks'))is dict and set(value['networks'])<={'none'}
  and value.get('caps_add')in(None,[])and value.get('volumes')in(None,{})and value.get('tmpfs')in(None,{})
  and value.get('binds')in(None,[])and value.get('host_mounts')in(None,[]),'flat_export_container')
 return value

def cli(ctx,old,stage,*,image,cid=None,output=None):
 """Only create/inspect/export; independent bounded public stdout and stderr."""
 need(stage in STAGES and old.DOCKER==['/usr/bin/docker','--host=unix:///var/run/docker.sock']
  and image_id(image),'flat_export_stage');name='app-ci-flat-acl-'+old.owner()
 if stage=='create':
  need(cid is None and output is None,'flat_export_stage');args=create_args(old,name,image);limit,seconds=256,30
 else:
  need(hex64(cid),'flat_export_stage')
  if stage in('preinspect','postinspect'):
   need(output is None,'flat_export_stage');args=['container','inspect','--format',inspect_template(old),cid];limit,seconds=16384,30
  else:
   need(type(output)is ExportFile and output.path==ctx.root/'whole-image-export.tar','flat_export_stage')
   args=['container','export',cid];limit,seconds=MAX_EXPORT,300
 ctx.check();end=min(ctx.end,time.monotonic()+seconds)
 environment={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','DOCKER_CONFIG':str(ctx.root/'docker-config')}
 child=subprocess.Popen([*old.DOCKER,*args],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
  env=environment,start_new_session=True,close_fds=True)
 buffers={'stdout':bytearray(),'stderr':bytearray()};counts={'stdout':0,'stderr':0};binding=None
 try:
  with selectors.DefaultSelector()as selector:
   for key,pipe in(('stdout',child.stdout),('stderr',child.stderr)):
    os.set_blocking(pipe.fileno(),False);selector.register(pipe.fileno(),selectors.EVENT_READ,key)
   while selector.get_map():
    ctx.check();left=end-time.monotonic();need(left>0,'flat_export_timeout')
    for event,_ in selector.select(min(.1,left)):
     key=event.data;maximum=limit if key=='stdout'else MAX_STDERR
     raw=os.read(event.fd,min(65536,maximum-counts[key]+1))
     if not raw:selector.unregister(event.fd);continue
     counts[key]+=len(raw)
     need(counts[key]<=maximum,'flat_export_stdout_bound'if key=='stdout'else'flat_export_stderr_bound')
     if key=='stdout'and output is not None:output.write(raw,ctx.check)
     else:buffers[key].extend(raw)
   try:code=child.wait(timeout=max(.01,end-time.monotonic()))
   except subprocess.TimeoutExpired:raise RuntimeError('flat_export_timeout')from None
   ctx.check();need(type(code)is int and code==0,'flat_export_client')
   # This binding/readback precedes selector, child-pipe and original writer closes.
   if output is not None:binding=output.freeze(ctx.check,min(ctx.end,time.monotonic()+120))
  ctx.check();return binding if output is not None else bytes(buffers['stdout'])
 finally:
  try:ctx.C.cleanup_owned_child(child)
  finally:
   close_error=False
   for pipe in(child.stdout,child.stderr):
    try:pipe.close()
    except BaseException:close_error=True
   ctx.C.write(ctx.root/'reports'/('flat-export-cli-'+stage+'.json'),encode({
    'schema':'public_flat_image_export_cli/1','stage':stage,
    'actual_client_returncode':child.returncode if type(child.returncode)is int else None,
    'stdout_bytes_observed':counts['stdout'],'stderr_bytes_observed':counts['stderr'],
    'stderr_text':bytes(buffers['stderr']).decode('utf-8',errors='replace'),
    'stderr_truncated':counts['stderr']>MAX_STDERR,
    'security_accepted':False}))
   ctx.check();need(not close_error,'flat_export_pipe_close')

def whole_image_inventory(ctx,old,image,control,sbom,*,I,T,A,adapter_sha256):
 """New cohort only after genuine image/source/SBOM admission by outer caller."""
 need(EXECUTION_ARMED is True,'flat_export_unarmed')
 need(type(control)is dict and control.get('image_id')==image and image_id(image)
  and type(control.get('source_commit'))is str and re.fullmatch('[a-f0-9]{40}',control['source_commit'])
  and hex64(control.get('sbom_sha256'))and type(sbom)is bytes and 0<len(sbom)<=4*1024**2
  and sha256(sbom).hexdigest()==control['sbom_sha256']and hex64(adapter_sha256),'flat_export_scope')
 keeper=ctx.C.PROOF_KEEPER
 need(keeper is not None and keeper.root==ctx.root and not keeper.closed,'flat_export_contract')
 name='app-ci-flat-acl-'+old.owner();ctx.created.append((name,image))
 cid=cli(ctx,old,'create',image=image).decode('ascii').strip();need(hex64(cid),'flat_export_receipt')
 ctx.C.write(ctx.root/'reports/flat-export-created.json',encode({'schema':'public_flat_image_export_created/1',
  'cid':cid,'name':name,'image_id':image,'owner':old.owner(),'started':False,'security_accepted':False}))
 before=admit_container(document(cli(ctx,old,'preinspect',image=image,cid=cid)),old,name,image,cid)
 output=ExportFile(ctx.C,keeper,ctx.root)
 binding=cli(ctx,old,'export',image=image,cid=cid,output=output)
 after=admit_container(document(cli(ctx,old,'postinspect',image=image,cid=cid)),old,name,image,cid)
 need(before==after,'flat_export_container');ctx.check()
 receipt={'schema':'same_image_never_started_export/1','container_id':cid,'image_id':image,
  'source_commit':control['source_commit'],'before':before,'after':after,'archive':binding,
  'actual_export_client_returncode':0,'container_started':False,'private_mounts_used':False,'security_accepted':False}
 ctx.C.write(ctx.root/'reports/flat-export-receipt.json',encode(receipt));ctx.check()
 # The independently frozen pure adapter's final signature is joined here by a
 # successor after its peer review; this proposal cannot execute while unarmed.
 with output.borrow(ctx.check)as stream:
  value=A.collect(I,T,stream,binding['bytes'],image_id=image,source_commit=control['source_commit'],
   sbom_raw=sbom,sbom_sha256=control['sbom_sha256'],verifier_sha256=adapter_sha256,
   export_sha256=binding['sha256'],container_receipt=receipt,check=ctx.check,end=min(ctx.end,time.monotonic()+MAX_INVENTORY_SECONDS))
 need(type(value)is dict and value.get('schema')=='image_acl_archive_inventory/1'
  and value.get('image_id')==image and value.get('source_commit')==control['source_commit']
  and value.get('security_accepted')is False,'flat_export_receipt')
 raw=encode(value);need(len(raw)<=MAX_REPORT,'flat_export_bound')
 ctx.C.write(ctx.root/'reports/fresh-whole-image-acl-inventory.json',raw);ctx.check();return value

if __name__=='__main__':print(json.dumps({'status':'UNARMED','export_performed':False,'security_accepted':False}))
