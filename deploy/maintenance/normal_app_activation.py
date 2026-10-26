"""Ordinary five-role App activation using fresh admitted normal per-role RAM.

The sealed C/S/coordinator stay unchanged. This entry does not import any
historical profile/COW controller. Default is CHECK; no retry or resume.
"""
from contextlib import ExitStack,contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,json,os,re,shlex,stat,subprocess,sys,tempfile,time

OP='security-normal-app-activation-20261026-01'
CONFIRM='ACTIVATE-ONLY-ORDINARY-CANONICAL-APP-665220A2-20261026-01'
ENTRY='normal_app_activation.py'
MACHINE_ID=Path('/etc/machine-id')
ROOTS={'bot':Path('/root/trading-bot/trading_bot/.local')/OP,'web':Path('/srv/trading-bot')/OP}
PINS={'canonical_app_activation_contract.py':'97e50fb2702b377a611599969fc96d5320442778a6cab8c0555b3cf576f35317',
 'canonical_app_activation_site.py':'d6c0f59c63fe8932e9c3a378eced3dbf7a993fd4caef78dd0d1c19b49d506a29',
 'canonical_app_activation_coordinator.py':'269dbb06a3c275878e6b7c55cc58b69419e3c3fe947a2b2b81d6a1712ae47df2',
 'normal_app_restore_admission.py':'8ba58bfb364655a091a165da704808f11d2680eacf1cc8fe65c4da06eab1cb2b',
 'bounded_operational_capture.py':'a223222cc8bf5221aafa49fd3d69307282dce884b8e998d49fe609c56078e2a0'}
SEMVER=r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?'
def need(value,code):
 if not value:raise RuntimeError(code)
def identity(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
@contextmanager
def cold(path,pin=None,maximum=131072):
 path=Path(path);parents=[]
 for p in path.parents:
  s=p.lstat();need(p.resolve(strict=True)==p and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and not s.st_mode&0o022,'source_parent')
  parents.append((p,identity(s)[:5]))
 before=identity(path.lstat());need(path.resolve(strict=True)==path and before[2]==0o100600 and before[3:6]==[0,0,1] and 0<before[6]<=maximum,'source_identity')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  need(identity(os.fstat(fd))==before,'source_identity');raw=os.pread(fd,before[6]+1,0)
  need(len(raw)==before[6] and (pin is None or sha256(raw).hexdigest()==pin),'source_pin')
  def check():
   need(all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in parents),'source_parent_changed')
   need(identity(os.fstat(fd))==before==identity(path.lstat()) and os.pread(fd,len(raw)+1,0)==raw,'source_changed')
  check();primary=False
  try:yield raw,check
  except BaseException:primary=True;raise
  finally:
   try:check()
   except BaseException:
    if not primary:raise
 finally:os.close(fd)
def module(raw,path):
 m=ModuleType('_normal_app_'+Path(path).stem);m.__file__=str(path);exec(compile(raw,str(path),'exec'),m.__dict__);return m
def document(raw):
 def unique(rows):
  value={}
  for key,item in rows:need(key not in value,'duplicate_json_key');value[key]=item
  return value
 return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'nonfinite_json'))
def unarmed():
 return {'schema':'normal_app_activation/1','operation':OP,'nonce':None,'controller_commit':None,'source_files':None,
  'issued_at':None,'forward_deadline':None,'recovery_deadline':None,'image':None,'runtime_env_delta':{},
  'sites':{'bot':None,'web':None},'gates':None,'writer_change_permitted':False,'dns_change_permitted':False,
  'security_accepted':False,'full_matrix_accepted':False}
def validate(C,A,value,now,*,recovery=False):
 need(type(value) is dict and set(value)==set(unarmed()) and value['schema']=='normal_app_activation/1' and value['operation']==OP,'control_scope')
 need(type(value['nonce']) is str and re.fullmatch('[a-f0-9]{32}',value['nonce']) and type(value['controller_commit']) is str and re.fullmatch('[a-f0-9]{40}',value['controller_commit']),'unarmed')
 start,end,finish=(value[k] for k in ('issued_at','forward_deadline','recovery_deadline'))
 need(all(type(v) is int for v in (start,end,finish)) and start<=now<(finish if recovery else end) and start<end<=start+5400 and end<finish<=end+1800,'authority_window')
 need(C.same(value['image'],C.IMAGE) and (C.same(value['runtime_env_delta'],{}) or C.same(value['runtime_env_delta'],{'PYTHONDONTWRITEBYTECODE':'1'})),'candidate_identity')
 need(all(value[k] is False for k in ('writer_change_permitted','dns_change_permitted','security_accepted','full_matrix_accepted')),'authority_scope')
 need(type(value['source_files']) is dict and set(value['source_files'])==set(PINS)|{ENTRY},'source_inventory')
 for name,binding in value['source_files'].items():
  need(type(binding) is dict and set(binding)=={'bytes','sha256'} and type(binding['bytes']) is int and 0<binding['bytes']<=131072 and C.hex64(binding['sha256']),'source_binding')
  if name in PINS:need(binding['sha256']==PINS[name],'source_binding')
 need(type(value['sites']) is dict and set(value['sites'])==set(C.SITES),'site_scope')
 for site,scope in value['sites'].items():
  need(type(scope) is dict and set(scope)=={'machine_id','fences','reader_units','legacy_units','public_image_env_delta','normal_admission','current_compose_version','docker_version'},'unarmed_site')
  need(type(scope['current_compose_version']) is str and re.fullmatch(SEMVER,scope['current_compose_version']) and type(scope['docker_version']) is str and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',scope['docker_version']),'engine_version')
  admission=scope['normal_admission'];A.controls(admission,start)
  need(admission['site']==site and admission['nonce']==value['nonce'] and admission['issued_at']==start and admission['machine_id']==scope['machine_id'],'normal_admission')
  for role,spec in admission['roles'].items():
   need(spec['project']==C.SITES[site]['project'] and spec['workdir']==C.SITES[site]['workdir'] and spec['compose_files'][0]['path']==spec['workdir']+('/docker-compose.yml' if site=='bot' else '/docker-compose.iran.yml'),'normal_prefix')
  for key,leaf in (('contract','canonical_app_activation_contract.py'),('utilities','canonical_app_activation_site.py'),('capture','bounded_operational_capture.py')):
   need(admission['helpers'][key]=={'path':str(ROOTS[site]/leaf),'sha256':PINS[leaf]},'normal_admission_source')
  need(type(scope['fences']) is dict and set(scope['fences'])==({'marker','source'} if site=='bot' else {'marker'}),'fence_binding')
  for key,row in scope['fences'].items():C.metadata(row,empty=key=='source')
  if site=='bot':need(C.same(scope['fences']['source'],C.SOURCE_ID),'original_source_identity')
  need(type(scope['reader_units']) is dict and set(scope['reader_units'])==(C.READER_UNITS if site=='bot' else set()),'reader_units')
  need(type(scope['legacy_units']) is dict and scope['legacy_units'],'legacy_units')
  for name,policy in {**scope['reader_units'],**scope['legacy_units']}.items():
   need(type(name) is str and re.fullmatch(r'[a-zA-Z0-9_.@-]+\.(?:service|timer)',name) and type(policy) is dict and policy and set(policy)<=C.UNIT_PUBLIC_FIELDS and all(type(v) is str for v in policy.values()),'unit_binding')
  for policy in scope['reader_units'].values():
   need(set(policy)<=C.READER_POLICY_FIELDS and {'LoadState','UnitFileState','NeedDaemonReload'}<=set(policy) and policy['LoadState']=='loaded' and policy['UnitFileState'] and policy['NeedDaemonReload']=='no','reader_unit_policy')
  for policy in scope['legacy_units'].values():
   need(policy.get('ActiveState') in {'inactive','failed'} and policy.get('MainPID')==policy.get('ControlPID')=='0' and policy.get('UnitFileState') in {'disabled','masked','not-found'} and policy.get('NeedDaemonReload')=='no','legacy_owner_policy')
  delta=scope['public_image_env_delta'];need(type(delta) is dict and set(delta)<=C.PUBLIC_ENV_DEFAULT_KEYS,'image_default_delta')
  for pair in delta.values():need(type(pair) is dict and set(pair)=={'old','new'} and all(type(v) is str and v and '\n' not in v for v in pair.values()),'image_default_delta')
 gates=value['gates'];need(type(gates) is dict and set(gates)=={'canonical_smoke','host_loads','backup_restore','schema_compatibility'},'gate_inventory')
 need(type(gates['host_loads']) is dict and set(gates['host_loads'])==set(C.SITES),'load_gate_missing')
 for binding in [gates['canonical_smoke'],*gates['host_loads'].values(),gates['backup_restore'],gates['schema_compatibility']]:C.file_binding(binding)
 return value

class Machine:
 """The public machine identity has its own exact modes, never an Env waiver."""
 def __init__(self,path,stack,expected):
  self.path=Path(path);self.parents=[]
  for parent in self.path.parents:
   s=parent.lstat();need(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and not s.st_mode&0o022,'machine_identity');self.parents.append((parent,identity(s)[:5]))
  self.before=identity(self.path.lstat())
  need(self.path.resolve(strict=True)==self.path and self.before[2] in {0o100444,0o100644} and self.before[3:7]==[0,0,1,33],'machine_identity')
  self.fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,self.fd)
  need(identity(os.fstat(self.fd))==self.before,'machine_identity');self.raw=os.pread(self.fd,34,0)
  need(self.raw==expected.encode()+b'\n' and re.fullmatch('[a-f0-9]{32}',expected),'machine_identity')
  self.check();stack.callback(self.final)
 def check(self):
  need(self.path.resolve(strict=True)==self.path and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in self.parents),'machine_changed')
  need(identity(os.fstat(self.fd))==self.before==identity(self.path.lstat()) and os.pread(self.fd,34,0)==self.raw,'machine_changed')
 def final(self):
  primary=sys.exc_info()[0] is not None
  try:self.check()
  except BaseException:
   if not primary:raise

def install(C,S,K,A,T,*,fences):
 """All modules are cold-loaded private copies; no process-wide old entry edit."""
 original_baseline=C.normal_baseline
 original_guard=S.Guard
 role_operands={};C.OPERATION=OP;C.CONFIRM=CONFIRM
 C.validate_control=lambda value,now,**kw:validate(C,A,value,now,**kw)
 def baseline(model,service,row,image,scope,files,**kw):
  if service in role_operands:
   old=role_operands[service];scope=deepcopy(old['scope']);scope['public_image_env_delta']=old['public_image_env_delta']
   if row['id']!=old['row']['id']:
    scope['compose_version']=old['current_version'];labels=row['config']['Labels']
    need(labels.get('com.docker.compose.replace')==model['services'][service]['container_name'] and labels.get('com.docker.compose.depends_on')=='','compose_labels')
    kw.update(replacement=True,creation_no_dependencies=True)
   kw['runtime_restart_policy']=old.get('runtime_restart_policy')
  return original_baseline(model,service,row,image,scope,files,**kw)
 C.normal_baseline=baseline
 class Guard(original_guard):
  def check(self):
   super().check()
   if self.closed:need(identity(os.fstat(self.held.fd))[:5]==self.held.before[:5],'guard_cleanup')
 S.Guard=Guard
 class Site(S.Site):
  def __init__(self,site,control,stack,*,apply=False):
   C.validate_control(control,int(time.time()));need(site in C.SITES and os.geteuid()==os.getegid()==0,'root_site_required')
   self.site,self.control,self.stack,self.apply=site,control,stack,apply;self.shared=control['sites'][site];self.scope=self.shared
   self.root=ROOTS[site];S.parents(self.root/'placeholder');need(self.root.stat().st_mode&0o7777==0o700,'root_mode')
   need(Path('/proc/self/ns/mnt').stat().st_ino==Path('/proc/1/ns/mnt').stat().st_ino,'host_namespace')
   self.deadline=time.monotonic()+control['forward_deadline']-time.time();self.checks=[];self.guard=None;self.journal=None;self.recovering=False
   machine=Machine(MACHINE_ID,fences,self.shared['machine_id']);self.checks.append(machine.check)
   if apply:self.scope_check()
   for key,path in [('marker',S.MARKER)]+([('source',S.SOURCE)] if site=='bot' else []):
    held=S.Held(path,fences,expected=self.shared['fences'][key],opaque=True,lock=True);self.checks.append(held.check)
   need(not os.path.lexists(S.RESERVATION if site=='bot' else self.root/'activation.guard'),'guard_busy')
   client=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.normal-activation-client-',dir=self.root)));client.chmod(0o700)
   need(client.stat().st_uid==client.stat().st_gid==0 and not list(client.iterdir()),'client_configuration')
   S.COMMAND_ENV={**S.COMMAND_ENV,'DOCKER_HOST':'unix:///var/run/docker.sock','DOCKER_CONFIG':str(client)}
   self.candidate=C.evidence(control,self.evidence,int(time.time()))[site]
   public,private=A.collect(self.shared['normal_admission'],stack,PINS['normal_app_restore_admission.py'])
   self.current_version=public['current_compose_version'];need(self.current_version==self.shared['current_compose_version'],'compose_version')
   self.admitted=private['roles'];self.original=self.inventory();self.current=deepcopy(self.original);self.images={};self.role_ids={}
   self.completed=[];self.transactions={};self.terminal=None;self.models={};self.by_role={};role_operands.clear()
   for role,values in self.admitted.items():
    self.bind_role(role,values)
   self.new_image=self.image(self.candidate);need(C.same(self.new_image['RootFS'],C.IMAGE['rootfs']) and self.new_image['Id']==self.candidate,'candidate_rootfs')
   for image in self.images.values():C.image_defaults(image['Config'],self.new_image['Config'],self.shared['public_image_env_delta'])
   for role in self.admitted:
    self.select(role);self.version_check();self.functional(role,self.current[self.role_ids[role]])
    new=self.render(override=self.override_bytes(),virtual=True);expected_old=deepcopy(self.old_model)
    if 'runtime_restart_policy' in self.admitted[role]:expected_old['services'][role]['restart']='always'
    C.rendered_delta(expected_old,new,(role,),self.candidate,control['runtime_env_delta'])
    rollback=self.render(override=self.override_bytes(candidate=False),virtual=True)
    expected=deepcopy(self.old_model);expected['services'][role]['image']=self.original[self.role_ids[role]]['image']
    # The source-admitted runtime always/0 witness is the actual normal policy,
    # separately bound from a historical restart:no creation override.
    policy=self.admitted[role].get('runtime_restart_policy')
    if policy is not None:expected['services'][role]['restart']='always'
    need(C.same(rollback,expected),'normal_render_delta');self.by_role[role].update(new_model=new,rollback_model=rollback)
   self.select(next(iter(self.admitted)));self.units_check();self.unchanged();self.check()
  def bind_role(self,role,values):
   actual=S.row(values['actual']);need(actual['id'] in self.original and C.same(actual,self.original[actual['id']]),'role_baseline')
   self.role_ids[role]=actual['id'];self.images[actual['image']]=values['image']
   role_operands[role]={'scope':values['scope'],'row':actual,'current_version':self.current_version,'public_image_env_delta':self.shared['public_image_env_delta'],
    **({'runtime_restart_policy':values['runtime_restart_policy']} if 'runtime_restart_policy' in values else {})}
   self.by_role[role]={'old_model':values['model'],'paths':[b['path'] for b in values['scope']['compose_files']],
    'override':self.root/'attempts'/self.control['nonce']/('image-only-'+role+'.yml'),'rollback_override':self.root/'attempts'/self.control['nonce']/('old-images-'+role+'.yml')}
  def select(self,role):
   need(role in self.admitted,'role_scope');self.selected=role;self.scope={**self.shared,**self.admitted[role]['scope']}
   for key,value in self.by_role[role].items():setattr(self,key,value)
  def version_check(self):
   need(self.docker('compose','version','--short',maximum=1024).decode().strip().removeprefix('v')==self.current_version,'compose_version')
   need(re.fullmatch(SEMVER,self.current_version),'compose_version')
   need(self.docker('version','--format','{{.Server.Version}}',maximum=1024).decode().strip()==self.shared['docker_version'],'docker_version')
  def cmd(self,argv,**kw):
   if kw.get('mutation'):need(self.apply and self.guard is not None and self.journal is not None,'apply_not_authorized')
   return super().cmd(argv,**kw)
  def compose(self,extra=False,rollback=False):
   args=['/usr/bin/docker','--host=unix:///var/run/docker.sock','compose','--project-directory',self.scope['workdir'],'--project-name',self.scope['project']]
   for proof in self.scope['env_files']:
    if proof['cli']:args+=['--env-file',proof['path']]
   for path in self.paths:args+=['--file',path]
   if extra:args+=['--file',str(self.override)]
   if rollback:args+=['--file',str(self.rollback_override)]
   return args
  def render(self,override=None,virtual=False,no_env_resolution=False):
   if not no_env_resolution:
    refs=super().render(no_env_resolution=True);allowed={r[k] for r in self.scope['env_files'] for k in ('path','target')}
    need(type(refs.get('services')) is dict,'normal_render')
    for service in refs['services'].values():
     for ref in service.get('env_file',[]):need((ref['path'] if type(ref) is dict else ref) in allowed,'unbound_env_reference')
   return super().render(override,virtual,no_env_resolution)
  def override_bytes(self,candidate=True):
   role=self.selected;spec={'image':self.candidate if candidate else self.original[self.role_ids[role]]['image']}
   policy=self.admitted[role].get('runtime_restart_policy')
   if policy is not None:spec['restart']='always'
   if candidate and self.control['runtime_env_delta']:spec['environment']=self.control['runtime_env_delta']
   return (json.dumps({'services':{role:spec}},sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
  def begin(self):
   need(self.apply and self.guard is None,'apply_not_authorized');self.unchanged()
   self.guard=S.Guard(S.RESERVATION if self.site=='bot' else self.root/'activation.guard',self.control,fences)
   self.journal=S.Journal(self.root/'attempts'/self.control['nonce'],self.control)
   for role in self.admitted:
    self.select(role)
    for path,candidate in ((self.override,True),(self.rollback_override,False)):
     raw=self.override_bytes(candidate);need(len(raw)<=C.MAX_CONTROL,'override_write')
     fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
     try:
      view=memoryview(raw)
      while view:n=os.write(fd,view);need(n>0,'override_write');view=view[n:]
      os.fsync(fd)
     finally:os.close(fd)
     S.sync_directory(path.parent);held=S.Held(path,self.stack,pin=sha256(raw).hexdigest());self.checks.append(held.check)
    need(C.same(self.render(override=self.override),self.new_model) and C.same(self.render(override=self.rollback_override),self.rollback_model),'normal_render_changed')
  def stop(self,service,cid):
   self.select(service)
   if not self.recovering:
    before=self.inspect(cid);need(C.same(before,self.current[cid]),'runtime_changed')
    baseline(self.old_model,service,before,self.images[before['image']],self.scope,self.paths,
     replacement='com.docker.compose.replace' in before['config']['Labels'],creation_no_dependencies=C.no_dependencies_label(before['config']['Labels']))
   return super().stop(service,cid)
  def replace(self,service):self.select(service);return super().replace(service)
  def rollback(self,service):self.select(service);return super().rollback(service)
  def start(self,service,value):
   self.select(service);fresh=self.inspect(value['id'])
   need(C.same(fresh,value) and fresh['running'] is False and fresh['pid']==0 and fresh['restarts']==0
    and fresh['started']=='0001-01-01T00:00:00Z' and fresh['paused'] is fresh['restarting'] is fresh['dead'] is False,'created_state_unproven')
   model=self.new_model if fresh['image']==self.candidate else self.rollback_model
   image=self.new_image if fresh['image']==self.candidate else self.images[fresh['image']]
   files=[*self.paths,str(self.override if fresh['image']==self.candidate else self.rollback_override)]
   baseline(model,service,fresh,image,self.scope,files,replacement=True,creation_no_dependencies=True)
   return super().start(service,fresh)
  def close(self):
   super().close()
   # Guard is on the outer stack; its exact unlinked FD/name check and all
   # permanent fences outlive the last inner source finalizer.
 S.Site=Site;S.C=C;S.ROOTS=ROOTS;K.C=C;K.S=S;K.ROOTS=ROOTS
 return Site

def load(site,nonce,stack,fences):
 need(os.geteuid()==os.getegid()==0 and re.fullmatch('[a-f0-9]{32}',nonce),'root_nonce_required')
 root=ROOTS[site];raw,check=stack.enter_context(cold(root/'controls'/(nonce+'.json')));control=document(raw);checks=[check];modules={}
 need(type(control.get('source_files')) is dict and set(control['source_files'])==set(PINS)|{ENTRY},'source_inventory')
 for leaf in [*PINS,ENTRY]:
  binding=control['source_files'][leaf];pin=PINS.get(leaf,binding['sha256']);body,verify=stack.enter_context(cold(root/leaf,pin));checks.append(verify)
  need(type(binding['bytes']) is int and len(body)==binding['bytes'] and binding['sha256']==pin,'source_binding')
  if leaf!=ENTRY:modules[leaf]=module(body,root/leaf)
  else:need(Path(__file__).resolve()==root/ENTRY,'installed_source_path')
 C,S,K,A,T=(modules[n] for n in PINS);install(C,S,K,A,T,fences=fences)
 validate(C,A,control,int(time.time()));return control,checks,(C,S,K,A,T)

# This bootstrap holds the selected wrapper through the entire peer lifetime.
BOOTSTRAP="""import os,stat,hashlib,sys
p,pin=sys.argv[1:3];s=os.lstat(p);ident=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_gid,v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
from pathlib import Path
parents=[]
for q in Path(p).parents:
 t=os.lstat(q);assert Path(q).resolve(strict=True)==q and stat.S_ISDIR(t.st_mode) and t.st_uid==t.st_gid==0 and not t.st_mode&0o022;parents.append((q,ident(t)[:5]))
assert stat.S_ISREG(s.st_mode) and s.st_mode==0o100600 and s.st_uid==s.st_gid==0 and s.st_nlink==1 and 0<s.st_size<=131072
fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
try:
 assert ident(os.fstat(fd))==ident(s);b=os.pread(fd,s.st_size+1,0);assert len(b)==s.st_size and hashlib.sha256(b).hexdigest()==pin
 sys.argv=[p,*sys.argv[3:]];g={'__name__':'_normal_activation_peer','__file__':p};exec(compile(b,p,'exec'),g);rc=g['main']()
finally:
 primary=sys.exc_info()[0] is not None
 try:
  assert ident(os.fstat(fd))==ident(s)==ident(os.lstat(p)) and os.pread(fd,len(b)+1,0)==b
  assert all(Path(q).resolve(strict=True)==q and ident(os.lstat(q))[:5]==old for q,old in parents)
 except BaseException:
  if not primary:raise
 finally:os.close(fd)
raise SystemExit(rc)
"""
def peer_argv(K,C,control,*,apply):
 need(type(apply) is bool,'apply_not_authorized');path=str(ROOTS['web']/ENTRY);pin=control['source_files'][ENTRY]['sha256']
 remote=['/usr/bin/systemd-run','--scope','--quiet','--collect','--unit=security-canonical-app-activation-'+control['nonce'],
  '-p','RuntimeMaxSec=7200s','-p','MemoryMax=768M','-p','MemorySwapMax=0','-p','TasksMax=64','-p','CPUQuota=100%',
  '-p','TimeoutStopSec=5s','-p','KillMode=control-group','-p','SendSIGKILL=yes','/usr/bin/env','--chdir='+C.SITES['web']['workdir'],
  '/usr/bin/python3','-I','-B','-c',BOOTSTRAP,path,pin,'--peer','--nonce',control['nonce']]
 if apply:remote+=['--apply','--confirm',CONFIRM]
 return [*K.SSH,shlex.join(remote)]
def main(argv=None):
 parser=argparse.ArgumentParser();parser.add_argument('--nonce',required=True);parser.add_argument('--apply',action='store_true');parser.add_argument('--confirm');parser.add_argument('--peer',action='store_true');args=parser.parse_args(argv)
 result=None;connection=None;control=None;K=None
 try:
  with ExitStack() as fences,ExitStack() as stack:
   control,checks,modules=load('web' if args.peer else 'bot',args.nonce,stack,fences);C,S,K,A,T=modules
   class Peer(K.Peer):
    def __init__(self,control,deadline):
     self.control,self.deadline,self.index=control,deadline,0
     self.child=subprocess.Popen(peer_argv(K,C,control,apply=args.apply),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=S.COMMAND_ENV,close_fds=True)
     try:self.receive('ready')
     except BaseException:
      try:self.close_client(known=False)
      except BaseException:pass
      raise
   K.Peer=Peer;apply=args.apply;need(not args.apply or args.confirm==CONFIRM,'confirmation_required')
   if args.peer:return K.peer(control,checks,stack,apply)
   S.publish(ROOTS['bot']/((('apply' if apply else 'check')+'-'+control['nonce']+'-request.json')),
    {'schema':'normal_app_activation_request_intent/1','operation':OP,'nonce':control['nonce'],'controller_commit':control['controller_commit'],
     'control_sha256':C.control_binding(control),'apply_requested':apply,'security_accepted':False})
   site=S.Site('bot',control,stack,apply=apply);connection=Peer(control,site.deadline);connection.request('check');site.unchanged()
   if not apply:
    connection.request('end-check');result={'schema':'normal_app_activation_result/1','operation':OP,'status':'checked_not_activated','nonce':control['nonce'],'security_accepted':False,'full_matrix_accepted':False}
   else:
    site.begin();connection.request('begin');status=K.sequence({'web':K.Remote(connection),'bot':site});site.final();connection.request('final')
    ack=connection.request('prepare-close');terminal=site.prepare_terminal(status);S.publish(site.journal.path/'web-terminal-ack.json',ack)
    connection.request('close',native_terminal=terminal);site.close();result={'schema':'normal_app_activation_result/1','operation':OP,'status':status,'nonce':control['nonce'],
     'image_source_commit':C.IMAGE['source_commit'],'image_config_id':C.IMAGE['image_id'],'security_accepted':False,'full_matrix_accepted':False};S.publish(site.journal.path/'result.json',result)
   for check in checks:check()
   connection.close_client(known=True);connection=None;C.public(result)
  print(json.dumps(result,sort_keys=True),flush=True);return 0
 except BaseException:
  if args.peer and K is not None and control is not None:
   try:K.write(1,K.frame(control,K.PEER_INDEX,'refused_or_unknown'),time.monotonic()+1)
   except BaseException:pass
  elif not args.peer:print(json.dumps({'schema':'normal_app_activation_result/1','operation':OP,'status':'refused_or_unknown','retry_allowed':False,'daemon_cancellation_proven':False,'security_accepted':False,'full_matrix_accepted':False}),flush=True)
  return 1
 finally:
  if connection is not None:
   try:connection.close_client(known=False)
   except BaseException:pass
if __name__=='__main__':raise SystemExit(main())
