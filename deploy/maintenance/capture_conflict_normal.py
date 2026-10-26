"""Fixed F79 normal-Compose diagnostic admission; CHECK is the default.

The original stopped-first lifecycle and its unknown-outcome rules stay sealed.
Private normal models/Config/Env are RAM operands, never public evidence.
"""
from contextlib import ExitStack,contextmanager
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,ast,json,os,re,shlex,stat,sys,tempfile,time

BASE_SHA='9d2b9f2850e9ac129418c33777f863777e6ce5d3d27ccc0ec3fedaa852501860'
HISTORICAL_SHA='24f0f2e49f2052c1425e60cbb72bfd24bc5d14af44a9646751effb0c4d019236'
REBASE_SHA='2d567b9d105dbf21c490ad47587cb7c7352969ce0f7be8673814a408790dd2af'
WIRE_SHA='ff32988c1b524abd145daac4f34d2b189900245ebeae033992dcfce44c4d82be'
# Exact shared normal validators frozen by Root; historical proof pins above
# remain independent and are never relabeled as these current validators.
PRIMITIVE_PINS={'contract':'c07784fd6364b39db1b25f4e0e73dc95f9be6b7128b10f37f12c49dd681de140',
 'site':'d6c0f59c63fe8932e9c3a378eced3dbf7a993fd4caef78dd0d1c19b49d506a29',
 'market':'fada99159718b089538e7099ae3b4904168ea806d9c63056140a4fc6097cc621',
 'capture':'a223222cc8bf5221aafa49fd3d69307282dce884b8e998d49fe609c56078e2a0'}
PRIMITIVE_NAMES={'contract':'canonical_app_activation_contract.py','site':'canonical_app_activation_site.py',
 'market':'normal_market_activation_current.py','capture':'bounded_operational_capture.py'}
CONFIRM='ADD-ONLY-CAPTURE2-CURRENT-NORMAL-DIAGNOSTIC-SOURCE-20261026-04'
SEMVER=r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?'
SECRET_PATHS=('/srv/trading-bot/secure/market-data/account2-config.json',
 '/srv/trading-bot/secure/market-data/account2-peer-hmac')
LEGACY_RUNTIME_ROOT=Path('/run/systemd/system')
LEGACY_PERSISTENT_ROOT=Path('/etc/systemd/system')
LEGACY_CGROUP_ROOT=Path('/sys/fs/cgroup')
MACHINE_ID=Path('/etc/machine-id')

def need(ok,code):
 if not ok:raise RuntimeError(code)
def utc_started(value,*,zero=False):
 if type(value) is not str or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z',value):return False
 try:datetime.fromisoformat(value[:-1]+'+00:00')
 except ValueError:return False
 return zero or value!='0001-01-01T00:00:00Z'
CODES=set(('unknown','peer','normal_peer','normal_observation'))
STATIC_CODES=frozenset(CODES)
PHASES=frozenset(('entry','bootstrap','control','web_bootstrap','web_refs','web_snapshot','web_legacy',
 'web_prefix','web_image','web_parity','web_rebase','web_lifecycle','web_finalization',
 'native_bootstrap','native_peer','native_finalization'))
PHASE='entry'
COLD_ERRORS={}
def phase(value):
 global PHASE
 need(type(value) is str and value in PHASES,'normal_phase');PHASE=value
def literal_codes(raw):
 global STATIC_CODES
 for node in ast.walk(ast.parse(raw)):
  if not isinstance(node,ast.Call):continue
  name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ''
  index=1 if name in {'need','require'} else 0 if name in {'RuntimeError','Refusal'} else None
  if index is not None and len(node.args)>index and isinstance(node.args[index],ast.Constant):
   value=node.args[index].value
   if type(value) is str and re.fullmatch('[a-z][a-z0-9_]{2,80}',value):CODES.add(value)
 STATIC_CODES=frozenset(CODES)
def identity(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
@contextmanager
def cold(path,pin,maximum=131072,mode=0o600):
 path=Path(path);ancestors=[]
 for parent in path.parents:
  s=parent.lstat();need(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0
   and not s.st_mode&0o022,'normal_source');ancestors.append((parent,identity(s)[:5]))
 before=identity(path.lstat());need(path.is_absolute() and path.resolve(strict=True)==path and before[2:6]==[stat.S_IFREG|mode,0,0,1]
  and 0<before[6]<=maximum,'normal_source')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  need(before==identity(os.fstat(fd)),'normal_source');raw=os.pread(fd,before[6]+1,0)
  need(len(raw)==before[6] and sha256(raw).hexdigest()==pin,'normal_source')
  def check():
   need(before==identity(os.fstat(fd))==identity(path.lstat()) and os.pread(fd,len(raw)+1,0)==raw
    and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==v for p,v in ancestors),'normal_source_changed')
  primary=False;check()
  try:yield raw,check
  except BaseException:primary=True;raise
  finally:
   try:check()
   except BaseException:
    if not primary:raise
 finally:os.close(fd)
def module(raw,path):
 literal_codes(raw);value=ModuleType('_normal_capture_'+Path(path).stem);value.__file__=str(path);exec(compile(raw,str(path),'exec'),value.__dict__);return value
def base_module(raw,path):
 # Exact type identity from this cold-held source, never an exception name or
 # an arbitrary RuntimeError subclass. Sealed H has Unknown, not Refused.
 need(type(raw) is bytes and sha256(raw).hexdigest()==BASE_SHA,'normal_source')
 value=module(raw,path);COLD_ERRORS.clear();COLD_ERRORS[value.Unknown]='Unknown';return value
def public_file(stack,path,pin,maximum=2*1024**2,mode=None):
 # Admit the exact named leaf before open, then bind that same full9 to FD
 # before any pread. Existing H callbacks retain the source until final exit.
 path=Path(path);selected=stat.S_IMODE(path.lstat().st_mode) if mode is None else mode
 need(selected in {0o600,0o644},'normal_source')
 raw,check=stack.enter_context(cold(path,pin,maximum,selected))
 if path.suffix=='.py':literal_codes(raw)
 return raw,check
def source_modules(H,control,stack,checks,*,pins=None):
 wanted=PRIMITIVE_PINS if pins is None else pins
 need(set(wanted)==set(PRIMITIVE_NAMES) and all(type(v) is str and re.fullmatch('[a-f0-9]{64}',v) for v in wanted.values()),'normal_helpers_unarmed')
 result={};root=H.ROOTS['web']/control['nonce']
 for name,leaf in PRIMITIVE_NAMES.items():
  row=control['normal']['helpers'][name];need(row['path']==str(root/leaf) and row['sha256']==wanted[name],'normal_helper')
  raw,check=public_file(stack,Path(row['path']),row['sha256'],131072,0o600);need(len(raw)==row['bytes'],'normal_helper')
  checks.append(check);result[name]=module(raw,row['path'])
 C,S,M,T=(result[k] for k in ('contract','site','market','capture'));S.C=C
 need(M.BASE_CONTRACT_SHA==wanted['contract'] and M.BASE_UTILITIES_SHA==wanted['site'],'normal_helper')
 return C,S,M,T

def validate_normal(H,value):
 normal=value['normal'];need(type(normal) is dict and set(normal)=={'helpers','current_compose_version','creation_compose_version','scope'},'normal_control')
 need(type(normal['helpers']) is dict and set(normal['helpers'])==set(PRIMITIVE_NAMES),'normal_control')
 for name,row in normal['helpers'].items():
  need(type(row) is dict and set(row)=={'path','bytes','sha256'} and type(row['path']) is str
   and row['path']==str(H.ROOTS['web']/value['nonce']/PRIMITIVE_NAMES[name]) and type(row['bytes']) is int
   and 0<row['bytes']<=131072 and type(row['sha256']) is str and re.fullmatch('[a-f0-9]{64}',row['sha256']),'normal_control')
 for key in ('current_compose_version','creation_compose_version'):
  need(type(normal[key]) is str and re.fullmatch(SEMVER,normal[key]),'normal_compose_version')
 scope=normal['scope']
 if scope is None:return
 need(type(scope) is dict and set(scope)=={'env_files','secret_files','baseline','docker_version'},'normal_control')
 need(type(scope['docker_version']) is str and re.fullmatch(SEMVER,scope['docker_version']),'normal_control')
 row=scope['baseline'];need(type(row) is dict and set(row)=={'container_id','image_id','pid','started','restarts'}
  and row['container_id']==H.OLD_CID and row['image_id']==H.IMAGE and type(row['pid']) is int and row['pid']>0
  and utc_started(row['started']) and type(row['restarts']) is int and row['restarts']>=0,'normal_control')
 for kind,paths in (('env_files',{value['compose']['env_reference']}),('secret_files',set(SECRET_PATHS))):
  refs=scope[kind];need(type(refs) is list and len(refs)==len(paths) and {r.get('path') for r in refs if type(r) is dict}==paths,'normal_references')
  for ref in refs:
   need(type(ref) is dict and set(ref)=={'path','target','identity','aliases','cli'} and type(ref['cli']) is bool
    and ref['cli'] is (kind=='env_files') and type(ref['target']) is str and Path(ref['target']).is_absolute()
    and type(ref['identity']) is list and len(ref['identity'])==9 and all(type(n) is int for n in ref['identity'])
    and type(ref['aliases']) is list and len(ref['aliases'])<=8,'normal_references')

def discovered_reference(path,*,secret=False):
 path=Path(path);current=path;aliases=[]
 for _ in range(9):
  before=identity(current.lstat())
  if not stat.S_ISLNK(before[2]):break
  need(before[2]==0o120777 and before[3]==0 and before[4] in ({0,10001} if secret else {0}) and before[5]==1
   and 0<before[6]<=4096 and str(current) not in {r['path'] for r in aliases},'normal_references')
  target=os.readlink(current);need(type(target) is str and 0<len(target)<=4096 and '\x00' not in target,'normal_references')
  need(before==identity(current.lstat()),'normal_reference_changed');aliases.append({'path':str(current),'identity':before})
  current=Path(os.path.normpath(target if target.startswith('/') else str(current.parent/target)))
 else:raise RuntimeError('normal_references')
 need(current.resolve(strict=True)==current and stat.S_ISREG(before[2]) and before[3]==0 and before[5]==1
  and before[4] in ({0,10001} if secret else {0}) and stat.S_IMODE(before[2]) in ({0o400,0o440,0o600,0o640} if secret else {0o600}), 'normal_references')
 return {'path':str(path),'target':str(current),'identity':before,'aliases':aliases,'cli':not secret}

def reference(H,C,S,M,ref,stack,checks,*,secret=False):
 # M.SecretReference never reads a payload; expected named metadata is
 # separately admitted before entering its retained-FD checker.
 current=discovered_reference(ref['path'],secret=secret);need(C.same(current,ref),'normal_reference_changed')
 held=M.SecretReference(ref,S,stack) if secret else S.OpaqueReference(ref,stack)
 checks.append(held.check);return held

def strip_diagnostic(H,model,row,path):
 copied=deepcopy(model);volumes=copied['services'][H.SERVICE].get('volumes',[])
 chosen=[v for v in volumes if v.get('target')==H.DEST]
 if not chosen:
  need(not any(m.get('Destination') in {H.DEST,H.FOUNDATION_DEST} for m in row['mounts']),'source_delta');return copied,row,False
 expected={'type':'bind','source':path,'target':H.DEST,'read_only':True,'bind':{'create_host_path':False}}
 need(len(chosen)==1 and H.same(chosen[0],expected),'source_delta');volumes.remove(chosen[0])
 need(not any(v.get('target')==H.FOUNDATION_DEST for v in volumes),'source_delta')
 return copied,H.remove_source_bind(row,path),True

def normal_parity(H,C,M,control,model,row,image,refs,*,current_version):
 model,row,added=strip_diagnostic(H,model,row,control['new_source']['path']);role=H.SERVICE;spec=model['services'][role]
 need('pull_policy' not in spec or type(spec['pull_policy']) is str and spec['pull_policy']=='never','normal_pull_policy')
 need(row['image']==image['Id']==H.IMAGE and spec['image']==H.IMAGE,'normal_identity')
 # The release is a private comparison operand from the pinned normal model,
 # not an OCI/prefix inference or a serialized diagnostic observation.
 release=spec.get('environment',{}).get('MARKET_PIPELINE_RELEASE_SHA')
 need(type(release) is str and re.fullmatch('[a-f0-9]{40}',release),'normal_environment')
 created=row['id']!=H.OLD_CID
 scope={'project':H.PROJECT,'workdir':control['compose']['directory'],'container_name':H.PROJECT+'-'+role+'-1',
  'compose_version':current_version if created else control['normal']['creation_compose_version'],
  'environment_file_label':control['compose']['env_reference'],'profiles':['foundation-template','web'],'public_sources':[],
  'secret_files':refs['secret_files'],'release':release}
 files=[r['path'] for r in control['compose']['files']]+([str(H.ROOTS['web']/control['nonce']/'diagnostic-source.yml')] if added else [])
 M.baseline(C,model,role,row,image,scope,files,replacement='com.docker.compose.replace' in row['config']['Labels'],
  creation_no_dependencies=True if created else C.no_dependencies_label(row['config']['Labels']))
 return True

def lifecycle(H,row,*,stopped=False,never_started=False):
 need(type(row.get('pid')) is int and type(row.get('running')) is bool and row.get('restarting') is False
  and utc_started(row.get('started'),zero=never_started) and type(row.get('restarts')) is int and row['restarts']>=0,'normal_lifecycle')
 if stopped:need(row['pid']==0 and row['running'] is False,'normal_lifecycle')
 else:need(row['pid']>0 and row['running'] is True,'normal_lifecycle')
 if never_started:need(row['restarts']==0 and row['started']=='0001-01-01T00:00:00Z','normal_lifecycle')

def guarded_source_change(H,original,parity,old,new,path,**kwargs):
 # Validate actual generated labels through normal C/M before making the
 # comparison-only projection expected by the sealed lifecycle comparator.
 parity(new);left,right=deepcopy(old),deepcopy(new)
 for key in ('com.docker.compose.version','com.docker.compose.depends_on'):
  if key in left['config']['Labels']:right['config']['Labels'][key]=left['config']['Labels'][key]
  else:right['config']['Labels'].pop(key,None)
 if 'com.docker.compose.replace' in right['config']['Labels']:
  right['config']['Labels']['com.docker.compose.replace']=H.SERVICE+'-1'
 original(left,right,path,**kwargs)

def verify_rebase(H,backend,old,image,stack,checks):
 control=backend.op;inputs={}
 for name,binding in [('worker',control['baked_source_worker']),('validator',control['baked_source_validator']),
  ('historical',control['historical_source_worker']),('proof',control['baked_source_proof']),*control['source_evidence'].items()]:
  raw,check=public_file(stack,Path(binding['path']),binding['sha256'],H.LIMIT,0o600)
  need(len(raw)==binding['bytes'],'normal_source');checks.append(check);inputs[name]=raw
 R=module(inputs['worker'],control['baked_source_worker']['path']);W=module(inputs['validator'],control['baked_source_validator']['path'])
 M=module(inputs['historical'],control['historical_source_worker']['path'])
 need(R.RESOLVER_SHA==W.RESOLVER_SHA==HISTORICAL_SHA and W.REBASE_SHA==REBASE_SHA and R.BASELINE==M.F79_IMAGE==H.IMAGE,'baked_source_proof')
 selected=[p for p in H.document(inputs['catalog'])['profiles'] if p['profile']=='f79cdda21c19'];need(len(selected)==1,'baked_source_proof');spec=selected[0]
 lineage=R.admit_lineage(inputs['profile_evidence'],spec)
 receipt,admitted,association=M.baked_load_evidence(H.document,spec,inputs['load_receipt'],inputs['load_control'],inputs['archive_admission'],REBASE_SHA,control['web_machine_id'])
 proof=W.admit(R,H.document(inputs['proof']),lineage,spec,admitted,association,image['RootFS']['Layers'])
 need(receipt['candidate_local_image_id'] in proof['source_identities'] and old['host']['ReadonlyRootfs'] is True,'baked_source_association')
 fresh=R.observe_old_fixed(lambda args:backend.command([*H.DOCKER,*args]),old['id'],H.IMAGE,proof['candidate_effective_metadata'])
 need(fresh['container_id']==old['id']==H.OLD_CID and fresh['runtime']['pid']==old['pid'],'source_container');backend.check()

def install_structure(H,*,fence_stack=None):
 original=H.validate_control
 def validate(value,now):
  extra={'normal','baked_source_validator','historical_source_worker'}
  fields={'schema','operation','nonce','source_commit','worker_sha256','issued_at','deadline','fences','native_machine_id','web_machine_id',
   'compose','old_source','new_source','foundation_source','public_profile_sha256','baked_source_worker','baked_source_proof','source_evidence'}|extra
  need(type(value) is dict and set(value)==fields and value['schema']=='capture_conflict_control/4','normal_control')
  need(type(value.get('nonce')) is str and re.fullmatch('[a-f0-9]{32}',value['nonce']),'normal_control');root=H.ROOTS['web']/value['nonce']
  for key,leaf,pin in (('baked_source_worker','rebased_f79_sources.py',REBASE_SHA),('baked_source_validator','rebased_f79_proof_contract.py',WIRE_SHA),
   ('historical_source_worker','normal_market_activation.py',HISTORICAL_SHA)):
   r=value[key];need(type(r) is dict and set(r)=={'path','bytes','sha256'} and r['path']==str(root/leaf)
    and r['sha256']==pin and type(r['bytes']) is int and 0<r['bytes']<=131072,'normal_control')
  need(type(value['source_evidence']) is dict and set(value['source_evidence'])=={'catalog','load_receipt','load_control','archive_admission','profile_evidence'},'normal_control')
  r=value['source_evidence']['profile_evidence'];need(type(r) is dict and set(r)=={'path','bytes','sha256'} and r['path']==str(root/'profile-evidence.zip')
   and type(r['bytes']) is int and r['bytes']==276161 and r['sha256']=='faa1ccede53c7c2243aeba4c4ab6f9fa170ee0b8d05441be2c6027cba7d08b42','normal_control')
  need(value['baked_source_proof'].get('path')==str(root/'rebased-sources.json'),'normal_control')
  common=deepcopy(value);common['schema']='capture_conflict_control/2'
  for key in extra:common.pop(key)
  common['source_evidence'].pop('profile_evidence');common['baked_source_worker']['path']=str(root/'normal_market_activation.py')
  common['baked_source_proof']['path']=str(root/'baked-sources.json');original(common,now)
  validate_normal(H,value);return value
 prior_fence=H.MetadataFence
 class Fence(prior_fence):
  def __init__(self,path,expected,stack):
   need(identity(Path(path).lstat())==expected and expected[2:6]==[0o100600,0,0,1],'fence_changed')
   outer=stack if fence_stack is None else fence_stack;super().__init__(path,expected,outer)
   def final():
    primary=sys.exc_info()[0] is not None
    try:self.check()
    except BaseException:
     if not primary:raise
   outer.callback(final)
 H.validate_control=validate;H.public_file=public_file;H.CONFIRM=CONFIRM;H.MetadataFence=Fence
 H.INSPECT=H.INSPECT[:-1]+',"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},"paused":{{json .State.Paused}},"dead":{{json .State.Dead}}}'
 H.NORMAL_FENCE_STACK=fence_stack
 return H

def normal_backend(H,C,S,M):
 base=H.Web
 class Web(base):
  def __init__(self,control,pin,root,stack,checks):
   super().__init__(control,pin,root,stack,checks);self.C=C;self.M=M;self.admitted_snapshot=None
   cwd=Path(control['compose']['directory']);need(Path.cwd().resolve(strict=True)==cwd.resolve(strict=True),'normal_cwd')
   before=identity(cwd.lstat());need(cwd.resolve(strict=True)==cwd and stat.S_ISDIR(before[2]) and before[3:5]==[0,0] and not before[2]&0o022,'normal_cwd')
   def cwd_check():need(before==identity(cwd.lstat()) and Path.cwd().resolve(strict=True)==cwd,'normal_cwd')
   # Registered before child holders: it runs after their final checks.
   checks.append(cwd_check)
   def final_cwd():
    primary=sys.exc_info()[0] is not None
    try:cwd_check()
    except BaseException:
     if not primary:raise
   stack.callback(final_cwd)
   phase('web_refs')
   selected=control['normal']['scope']
   self.refs={
    'env_files':[discovered_reference(control['compose']['env_reference'])],
    'secret_files':[discovered_reference(p,secret=True) for p in SECRET_PATHS]}
   if selected is not None:
    need(C.same(self.refs['env_files'],selected['env_files']) and C.same(self.refs['secret_files'],selected['secret_files']),'normal_reference_changed')
   for kind,rows in self.refs.items():
    for ref in rows:reference(H,C,S,M,ref,stack,checks,secret=kind=='secret_files')
   self.client=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.normal-client-',dir=root)))
   self.client.chmod(0o700);need(self.client.stat().st_uid==self.client.stat().st_gid==0 and not list(self.client.iterdir()),'normal_client')
   self.current_version=control['normal']['current_compose_version'];self.image=None;self.old_render=None;self.new_render=None
  def command(self,argv,**kwargs):
   # All client state belongs to this operation; no personal Docker config.
   return super().command(['/usr/bin/env','-i','PATH=/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL=C.UTF-8',
    'DOCKER_HOST=unix:///var/run/docker.sock','DOCKER_CONFIG='+str(self.client),*argv],**kwargs)
  def bind_cgroup(self,row):
   self.check();empty=S.bind_cgroup(self.stack,row)
   current=self.target(self.snapshot());need(current['id']==row['id'] and current['pid']==row['pid'],'old_owner')
   def verified_empty():self.check();empty()
   return verified_empty
  def legacy_owners(self):
   # Runtime drop-ins disappear after reboot. The sealed persistent inhibitor
   # is the same authority; never recreate a runtime file to satisfy admission.
   import dbus
   need(not os.path.lexists(H.LEGACY_TOKEN),'session_owner');self.check()
   bus=dbus.SystemBus(private=True);held=[]
   try:
    manager=dbus.Interface(bus.get_object('org.freedesktop.systemd1','/org/freedesktop/systemd1'),'org.freedesktop.systemd1.Manager')
    for name in H.LEGACY_UNITS:
     manager.RefUnit(name,timeout=5);held.append(name);obj=manager.GetUnit(name,timeout=5)
     props=dbus.Interface(bus.get_object('org.freedesktop.systemd1',obj),'org.freedesktop.DBus.Properties')
     unit=lambda key:props.Get('org.freedesktop.systemd1.Unit',key,timeout=5)
     service=lambda key:props.Get('org.freedesktop.systemd1.Service',key,timeout=5)
     need(str(unit('LoadState'))=='loaded' and str(unit('UnitFileState'))=='disabled'
      and str(unit('ActiveState')) in {'inactive','failed'} and bool(unit('RefuseManualStart'))
      and not bool(unit('NeedDaemonReload')) and int(service('MainPID'))==int(service('ControlPID'))==0,'session_owner')
     need(any(list(row[:4])==['ConditionPathExists',False,False,str(H.LEGACY_TOKEN)] for row in unit('Conditions')),'session_owner')
     runtime=LEGACY_RUNTIME_ROOT/(name+'.d')/'market-container-authority.conf'
     persistent=LEGACY_PERSISTENT_ROOT/(name+'.d')/'90-security-20261024-web-boot.conf'
     files=set()
     for path,body in ((runtime,H.LEGACY_BODY),(persistent,H.LEGACY_HEADER+H.LEGACY_BODY)):
      if os.path.lexists(path):
       _,check=public_file(self.stack,path,sha256(body).hexdigest(),8192);self.checks.append(check);files.add(str(path))
      else:
       def absent(path=path):need(not os.path.lexists(path),'session_owner')
       def final(absent=absent):
        primary=sys.exc_info()[0] is not None
        try:absent()
        except BaseException:
         if not primary:raise
       absent();self.checks.append(absent);self.stack.callback(final)
     actual=list(map(str,unit('DropInPaths')))
     need(files and len(actual)==len(set(actual)) and set(actual)==files,'session_owner')
     group=str(service('ControlGroup'))
     if group:
      need(group.startswith('/') and '..' not in Path(group).parts,'cgroup');folder=LEGACY_CGROUP_ROOT/group.lstrip('/')
      if folder.exists():
       with (H.canonical(folder)/'cgroup.events').open('rb') as source:raw=source.read(4097)
       need(len(raw)<=4096 and dict(line.split() for line in raw.splitlines()).get(b'populated')==b'0','session_owner')
   finally:
    for name in reversed(held):
     try:manager.UnrefUnit(name,timeout=5)
     except Exception:pass
    bus.close()
   need(not os.path.lexists(H.LEGACY_TOKEN),'session_owner');self.check()
  def raw_render(self,new=False,public_override=None,*,references=False):
   tail=['config','--format','json']+(['--no-env-resolution'] if references else [])
   if public_override is None:return H.document(self.command(self.compose(*tail,new=new)))
   fd=os.memfd_create('normal-public-override',os.MFD_CLOEXEC)
   try:
    raw=H.encode(public_override);need(len(raw)<=65536 and os.write(fd,raw)==len(raw),'compose');os.lseek(fd,0,os.SEEK_SET)
    return H.document(self.command(self.compose(*tail,new=True,override='-'),stdin=fd))
   finally:os.close(fd)
  def render(self,new=False,public_override=None):
   visible=self.raw_render(new,public_override,references=True);allowed={r[k] for r in self.refs['env_files'] for k in ('path','target')}
   need(type(visible.get('services')) is dict and 0<len(visible['services'])<=64,'normal_references')
   for service in visible['services'].values():
    need(type(service) is dict and type(service.get('env_file',[])) is list,'normal_references')
    for ref in service.get('env_file',[]):
     path=ref.get('path') if type(ref) is dict else ref;need(type(path) is str and path in allowed,'normal_unbound_env')
   return self.raw_render(new,public_override)
  def full_parity(self,render,row):
   lifecycle(H,row,stopped=row['running'] is False,never_started=row['running'] is False and row['id']!=H.OLD_CID)
   if row['id']!=H.OLD_CID:need(row['restarts']==0,'normal_lifecycle')
   return normal_parity(H,C,M,self.op,render,row,self.image,self.refs,current_version=self.current_version)
  def parity_row(self,row):
   bound=any(m.get('Destination')==H.DEST for m in row['mounts'])
   return self.full_parity(self.new_render if bound else self.old_render,row)
  def stop(self,new=False):
   fresh=self.snapshot();target=self.target(fresh)
   if not self.requested:
    self.parity_row(target);need(H.same(fresh,self.admitted_snapshot),'normal_before_stop_changed');lifecycle(H,target)
   # A known, owned rejected candidate must remain stoppable for rollback.
   # H.rollback supplies its exact never-started/cgroup proof on that path.
   return super().stop(new)
  def start(self,cid):
   fresh=self.snapshot();target=self.target(fresh);need(target['id']==cid,'target');lifecycle(H,target,stopped=True,never_started=True)
   self.parity_row(target);result=super().start(cid)
   target=self.target(self.snapshot());need(target['id']==cid and target['restarts']==0,'normal_lifecycle');lifecycle(H,target)
   return result
 return Web

def detach_closed_guard(H,guard,checks,stack):
 guard.close_known();checks.remove(guard.check)
 outer=getattr(H,'NORMAL_FENCE_STACK',None) or stack;fd=os.dup(guard.fd);outer.callback(os.close,fd)
 def check_closed():
  after=H.ident(os.fstat(fd));need(after[:5]==guard.before[:5] and after[5]==0 and after[6:8]==guard.before[6:8]
   and not os.path.lexists(guard.path),'owner_changed')
 checks.append(check_closed)
 def final():
  primary=sys.exc_info()[0] is not None
  try:check_closed()
  except BaseException:
   if not primary:raise
 outer.callback(final)

def machine_file(stack,expected):
 path=MACHINE_ID;parents=[]
 for parent in path.parents:
  s=parent.lstat();need(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode)
   and s.st_uid==s.st_gid==0 and not s.st_mode&0o022,'normal_machine')
  parents.append((parent,identity(s)[:5]))
 before=identity(path.lstat());need(path.resolve(strict=True)==path and before[2] in {0o100444,0o100644}
  and before[3:7]==[0,0,1,33],'normal_machine')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,fd)
 need(identity(os.fstat(fd))==before,'normal_machine');raw=os.pread(fd,34,0)
 need(type(expected) is str and re.fullmatch('[a-f0-9]{32}',expected) and raw==expected.encode()+b'\n','normal_machine')
 def check():
  need(before==identity(os.fstat(fd))==identity(path.lstat()) and path.resolve(strict=True)==path
   and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==v for p,v in parents),'normal_machine')
  need(os.pread(fd,34,0)==raw,'normal_machine')
 def final():
  primary=sys.exc_info()[0] is not None
  try:check()
  except BaseException:
   if not primary:raise
 stack.callback(final);check();return check

def normal_load(H,args,stack):
 # Preserve sealed H.load admission, replacing only its unadmitted machine read.
 need(os.geteuid()==0,'root')
 need(type(args.nonce) is str and re.fullmatch('[a-f0-9]{32}',args.nonce)
  and type(args.control_sha256) is str and re.fullmatch('[a-f0-9]{64}',args.control_sha256),'control')
 root=H.ROOTS[args.site]/args.nonce;H.canonical(root)
 need(root.lstat().st_uid==0 and stat.S_IMODE(root.lstat().st_mode)==0o700 and re.fullmatch('[a-f0-9]{32}',args.nonce),'root')
 need(not any(os.path.lexists(root/name) for name in ('01-prepared.json','terminal.json','ack.json')),'owner_exists')
 raw,check=H.public_file(stack,root/'control.json',args.control_sha256,mode=0o600)
 control=H.validate_control(H.document(raw),time.time());need(control['nonce']==args.nonce,'control');checks=[check]
 raw,check=H.public_file(stack,Path(__file__).resolve(),control['worker_sha256']);checks.append(check);literal_codes(raw)
 checks.append(machine_file(stack,control[args.site+'_machine_id']))
 if args.site=='web':
  unit='capture-conflict-web-'+control['nonce']+'.scope'
  with Path('/proc/self/cgroup').open('rb') as source:group=source.read(65537)
  need(0<len(group)<=65536 and group.startswith(b'0::/') and group.decode().strip().endswith('/'+unit),'root')
  raw=H.bounded(['/usr/bin/systemctl','show',unit,'--property=RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL'],H.Clock(control),maximum=4096)
  fields=dict(line.split('=',1) for line in raw.decode().splitlines())
  need(set(fields)=={'RuntimeMaxUSec','TimeoutStopUSec','KillMode','SendSIGKILL'}
   and fields['RuntimeMaxUSec'] in {'15min','15min 0s','900s'} and fields['TimeoutStopUSec']=='5s'
   and fields['KillMode']=='control-group' and fields['SendSIGKILL']=='yes','root')
 for name,path in (('marker',H.MARKER),*([('source',H.SOURCE)] if args.site=='native' else [])):
  fence=H.MetadataFence(path,control['fences'][args.site][name],stack);checks.append(fence.check)
 if args.site=='web':
  for row in [*control['compose']['files'],control['new_source']]:
   _,check=H.public_file(stack,Path(row['path']),row['sha256'],maximum=256*1024,
    mode=0o644 if row is control['new_source'] else None);checks.append(check)
  checks.append(H.private_metadata(stack,Path(control['compose']['env_reference'])))
 return root,control,checks

def web_run(H,control,pin,root,stack,checks,apply=False,*,observe=False,pins=None):
 phase('web_bootstrap')
 need(not os.path.lexists(H.WEB_OWNER),'owner_exists')
 need((control['normal']['scope'] is None)==observe and not(observe and apply),'normal_control')
 C,S,M,T=source_modules(H,control,stack,checks,pins=pins);Web=normal_backend(H,C,S,M)
 backend=Web(control,pin,root,stack,checks);phase('web_snapshot');baseline=backend.snapshot();old=backend.target(baseline);lifecycle(H,old)
 need(old['id']==H.OLD_CID and H.public_profile(baseline)==control['public_profile_sha256'],'profile')
 need(len(old['mounts'])==5 and not any(m['Destination']==p or p.startswith(m['Destination'].rstrip('/')+'/')
  or m['Destination'].startswith(p+'/') for m in old['mounts'] for p in (H.DEST,H.FOUNDATION_DEST)),'source_delta')
 phase('web_prefix');H.provenance(old,[r['path'] for r in control['compose']['files']],control['compose']['env_reference'])
 phase('web_legacy');H.session_owners(baseline,old['id']);backend.legacy_owners()
 phase('web_prefix')
 current=backend.command([*H.DOCKER,'compose','version','--short']).decode().strip().removeprefix('v')
 need(current==control['normal']['current_compose_version'] and re.fullmatch(SEMVER,current),'normal_compose_version')
 docker_version=backend.command([*H.DOCKER,'version','--format','{{.Server.Version}}']).decode().strip();need(re.fullmatch(SEMVER,docker_version),'normal_docker_version')
 scope={**backend.refs,'docker_version':docker_version,'baseline':{'container_id':old['id'],'image_id':old['image'],
  'pid':old['pid'],'started':old['started'],'restarts':old['restarts']}}
 if not observe:need(C.same(scope,control['normal']['scope']),'normal_baseline_changed')
 fmt='{"Id":{{json .Id}},"Config":{{json .Config}},"RootFS":{{json .RootFS}},"Os":{{json .Os}},"Architecture":{{json .Architecture}}}'
 phase('web_image');backend.image=H.document(backend.command([*H.DOCKER,'image','inspect','--format',fmt,H.IMAGE]))
 need(set(backend.image)=={'Id','Config','RootFS','Os','Architecture'} and backend.image['Os']=='linux' and backend.image['Architecture']=='amd64','normal_image')
 phase('web_parity');backend.old_render=backend.render();backend.full_parity(backend.old_render,old)
 phase('web_rebase');verify_rebase(H,backend,old,backend.image,stack,checks)
 volume={'type':'bind','source':control['new_source']['path'],'target':H.DEST,'read_only':True,'bind':{'create_host_path':False}}
 phase('web_parity');override={'services':{H.SERVICE:{'volumes':[volume]}}};backend.new_render=backend.render(new=True,public_override=override)
 H.rendered_delta(backend.old_render,backend.new_render,control['new_source']['path']);backend.check()
 need(H.same(baseline,backend.snapshot()),'normal_runtime_changed');backend.admitted_snapshot=deepcopy(baseline)
 if observe:
  value={'schema':'capture_conflict_normal_observation/1','status':'normal_observation_passed_not_applied','nonce':control['nonce'],
   'control_sha256':pin,'observed_at':int(time.time()),'normal_scope':scope,'current_compose_version':current,'creation_compose_version':control['normal']['creation_compose_version'],
   'public_profile_sha256':control['public_profile_sha256'],'worker_sha256':control['worker_sha256'],
   'diagnostic_applied':False,'activation_authority':False,'capture_issue_open':True,'security_accepted':False}
  C.public(value);return value
 if not apply:return H.result(control,pin,'preflight_passed_not_applied')
 phase('web_lifecycle')
 need(backend.clock.end-time.monotonic()>360,'window');guard=H.OwnedGuard(H.WEB_OWNER,control['nonce'],pin,stack);checks.append(guard.check)
 backend.journal=H.Journal(root,control['nonce'],pin);backend.journal.add('prepared',full_config_parity=True)
 H.write_new(backend.override,override);need(H.same(backend.render(new=True),backend.new_render),'compose');backend.check()
 original=H.expected_source_change
 H.expected_source_change=lambda a,b,p,**kw:guarded_source_change(H,original,backend.parity_row,a,b,p,**kw)
 try:
  try:
   new=H.replacement(backend,baseline,backend.old_render,backend.new_render)
   try:record=backend.observe(new['id'])
   except BaseException:record=None
   value=H.result(control,pin,'diagnostic_active_incident_open',cid=new['id'],record=record)
   backend.journal.add('diagnostic_active_incident_open',container_id=new['id'],diagnostic=record)
  except H.Unknown:backend.journal.add('outcome_unknown');raise
  except BaseException:
   if not backend.requested:value=H.result(control,pin,'refused_before_stop');backend.journal.add('refused_before_stop')
   else:
    try:
     restored=H.rollback(backend,baseline,backend.old_render);value=H.result(control,pin,'rolled_back_incident_open',cid=restored['id'])
     backend.journal.add('rolled_back_incident_open',container_id=restored['id'])
    except BaseException:backend.journal.add('outcome_unknown');raise H.Unknown() from None
  backend.check();terminal=H.write_new(root/'terminal.json',value);backend.journal.add('terminal_durable')
  detach_closed_guard(H,guard,checks,stack);backend.journal.add('guard_closed')
  ack={**value,'terminal_sha256':terminal,'web_guard_closed':True};H.write_new(root/'ack.json',ack);return ack
 finally:H.expected_source_change=original

BOOTSTRAP='''from hashlib import sha256
import os,pathlib,stat,sys
p=pathlib.Path(sys.argv[1]);pin=sys.argv[2]
identity=lambda s:[s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
parents=[]
for path in p.parents:
 s=path.lstat();assert path.resolve(strict=True)==path and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and not s.st_mode&0o022
 parents.append((path,identity(s)[:5]))
before=identity(p.lstat());assert p.is_absolute() and p.resolve(strict=True)==p and before[2:6]==[0o100600,0,0,1] and 0<before[6]<=131072
fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
try:
 assert before==identity(os.fstat(fd));raw=os.pread(fd,before[6]+1,0);assert len(raw)==before[6] and sha256(raw).hexdigest()==pin
 def check():
  assert before==identity(os.fstat(fd))==identity(p.lstat()) and os.pread(fd,len(raw)+1,0)==raw
  assert all(path.resolve(strict=True)==path and identity(path.lstat())[:5]==v for path,v in parents)
 check();sys.argv=sys.argv[1:2]+sys.argv[3:]
 try:exec(compile(raw,str(p),'exec'),{'__name__':'__main__','__file__':str(p)})
 finally:
  primary=sys.exc_info()[0] is not None
  try:check()
  except BaseException:
   if not primary:raise
finally:os.close(fd)
'''

def refusal(error):
 classes=(RuntimeError,OSError,PermissionError,FileNotFoundError,NotADirectoryError,KeyError,ValueError,TypeError,AssertionError)
 admitted=type(error) is RuntimeError or type(error) in COLD_ERRORS
 code=error.args[0] if admitted and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in STATIC_CODES else 'unknown'
 number=error.errno if isinstance(error,OSError) and type(error.errno) is int and 0<error.errno<=4095 else None
 return {'schema':'capture_conflict_normal_refusal/2','status':'unknown_or_refused','code':code,'phase':PHASE,
  'exception_class':type(error).__name__ if type(error) in classes else COLD_ERRORS.get(type(error),'other'),'os_errno':number,
  'retry_allowed':False,'capture_issue_open':True,'security_accepted':False,'functional_fix_claimed':False}
def peer_refusal(value):
 need(type(value) is dict and set(value)=={'schema','status','code','phase','exception_class','os_errno','retry_allowed','capture_issue_open','security_accepted','functional_fix_claimed'}
  and value['schema']=='capture_conflict_normal_refusal/2' and value['status']=='unknown_or_refused' and type(value['code']) is str
  and value['code'] in STATIC_CODES and type(value['phase']) is str and value['phase'] in PHASES
  and value['exception_class'] in {'RuntimeError','Unknown','OSError','PermissionError','FileNotFoundError','NotADirectoryError','KeyError','ValueError','TypeError','AssertionError','other'}
  and (value['os_errno'] is None or type(value['os_errno']) is int and 0<value['os_errno']<=4095)
  and value['retry_allowed'] is False and value['capture_issue_open'] is True and value['security_accepted'] is value['functional_fix_claimed'] is False,'normal_peer')
 return value
def observation(H,value,control,pin):
 fields={'schema','status','nonce','control_sha256','observed_at','normal_scope','current_compose_version','creation_compose_version','public_profile_sha256','worker_sha256',
  'diagnostic_applied','activation_authority','capture_issue_open','security_accepted'}
 need(type(value) is dict and set(value)==fields and value['schema']=='capture_conflict_normal_observation/1'
  and value['status']=='normal_observation_passed_not_applied' and value['nonce']==control['nonce'] and value['control_sha256']==pin
  and value['worker_sha256']==control['worker_sha256'] and value['public_profile_sha256']==control['public_profile_sha256']
  and type(value['observed_at']) is int and control['issued_at']<=value['observed_at']<=time.time()<control['deadline']
  and value['current_compose_version']==control['normal']['current_compose_version'] and value['creation_compose_version']==control['normal']['creation_compose_version']
  and value['diagnostic_applied'] is value['activation_authority'] is value['security_accepted'] is False and value['capture_issue_open'] is True,'normal_observation')
 copy=deepcopy(control);copy['normal']['scope']=value['normal_scope'];validate_normal(H,copy);return value

def native_run(H,args,root,control,checks,stack,*,capture=None):
 phase('native_bootstrap')
 apply=args.apply==CONFIRM;observe=args.observe;need(not os.path.lexists(H.RESERVATION),'owner_exists')
 need(not(observe and apply) and (control['normal']['scope'] is None)==observe,'normal_control');clock=H.Clock(control)
 guard=journal=None;mode='observe' if observe else 'apply' if apply else 'check'
 request=root/('native-'+mode+'-request.json');need(not os.path.lexists(request),'owner_exists')
 raw,check=public_file(stack,root/PRIMITIVE_NAMES['capture'],PRIMITIVE_PINS['capture'],131072,0o600);checks.append(check);T=module(raw,root/PRIMITIVE_NAMES['capture'])
 # Admit exactly the sealed peer's failure vocabulary before the request;
 # Native never loads or executes the normal helpers against live state.
 for name in ('contract','site','market'):
  binding=control['normal']['helpers'][name];need(binding['sha256']==PRIMITIVE_PINS[name],'normal_helper')
  raw,check=public_file(stack,root/PRIMITIVE_NAMES[name],binding['sha256'],131072,0o600)
  need(len(raw)==binding['bytes'],'normal_helper');checks.append(check);literal_codes(raw)
 for final in checks:final()
 if apply:
  need(clock.end-time.monotonic()>360,'window');guard=H.OwnedGuard(H.RESERVATION,control['nonce'],args.control_sha256,stack);checks.append(guard.check)
  journal=H.Journal(root,control['nonce'],args.control_sha256);journal.add('prepared')
 H.write_new(request,{'schema':'capture_conflict_normal_request/1','nonce':control['nonce'],'control_sha256':args.control_sha256,
  'mode':mode,'retry_allowed':False,'diagnostic_request_intended':apply,'security_accepted':False})
 remote=H.ROOTS['web']/args.nonce/'capture_conflict_diagnostic.py'
 argv=['/usr/bin/python3','-I','-B','-c',BOOTSTRAP,str(remote),control['worker_sha256'],'--site','web','--nonce',args.nonce,
  '--control-sha256',args.control_sha256,'--coordinator-request',args.control_sha256]
 if observe:argv.append('--observe')
 if apply:argv+=['--apply',CONFIRM]
 scope=['/usr/bin/systemd-run','--scope','--quiet','--collect','--unit=capture-conflict-web-'+args.nonce,
  '--property=RuntimeMaxSec=900','--property=TimeoutStopSec=5','--property=KillMode=control-group','--property=SendSIGKILL=yes',
  '--property=MemoryMax=1G','--property=MemorySwapMax=0','--property=TasksMax=64','--property=CPUQuota=100%',
  '/usr/bin/env','--chdir='+control['compose']['directory'],*argv]
 if journal:journal.add('peer_requested')
 phase('native_peer')
 try:
  rc,raw=(capture or T.capture)([*H.SSH,shlex.join(scope)],min(900,max(.1,clock.end-time.monotonic())),
   environment={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C.UTF-8'})
  need(type(rc) is int and type(raw) is bytes and 0<len(raw)<=H.LIMIT,'normal_peer');value=H.document(raw)
  if rc==1:
   failure=peer_refusal(value)
   if journal:journal.add('outcome_unknown')
   value={'schema':'capture_conflict_normal_peer_refusal/1','status':'peer_refused_or_unknown','nonce':control['nonce'],
    'control_sha256':args.control_sha256,'peer_failure':failure,'request_issued':apply,'retry_allowed':False,
    'capture_issue_open':True,'security_accepted':False}
   H.write_new(root/('native-'+mode+'-outcome.json'),value);return value
  need(rc==(3 if apply else 0),'normal_peer')
  value=observation(H,value,control,args.control_sha256) if observe else H.peer_result(value,control,args.control_sha256)
  if not observe:need(apply==(value['status']!='preflight_passed_not_applied'),'normal_peer')
  for final in checks:final()
  if journal:
   H.write_new(root/'terminal.json',value);journal.add('terminal_durable');detach_closed_guard(H,guard,checks,stack);journal.add('guard_closed')
  H.write_new(root/('native-'+mode+'-outcome.json'),value)
  return value
 except BaseException:
  if journal:journal.add('outcome_unknown')
  raise H.Unknown() from None

def main(argv=None):
 try:
  phase('bootstrap')
  with ExitStack() as fences,ExitStack() as stack:
   path=Path(__file__).parent/'capture_conflict_diagnostic_base.py';raw,check=stack.enter_context(cold(path,BASE_SHA));H=base_module(raw,path);install_structure(H,fence_stack=fences)
   H.__file__=__file__  # H.load must seal the actual wrapper, not the held base.
   parser=argparse.ArgumentParser(add_help=False);parser.error=lambda _:need(False,'control')
   parser.add_argument('--site',choices=('native','web'),required=True);parser.add_argument('--nonce',required=True)
   parser.add_argument('--control-sha256',required=True);parser.add_argument('--apply');parser.add_argument('--coordinator-request');parser.add_argument('--observe',action='store_true')
   args=parser.parse_args(argv);need(args.apply in {None,CONFIRM} and not(args.observe and args.apply),'control')
   need(args.site!='web' or args.coordinator_request==args.control_sha256,'peer')
   phase('control');root,control,checks=normal_load(H,args,stack);checks.append(check)
   value=native_run(H,args,root,control,checks,stack) if args.site=='native' else web_run(H,control,args.control_sha256,root,stack,checks,args.apply==CONFIRM,observe=args.observe)
   phase('native_finalization' if args.site=='native' else 'web_finalization')
   for final in checks:final()
  print(json.dumps(value,sort_keys=True));return 1 if value['status']=='peer_refused_or_unknown' else 3 if args.apply else 0
 except BaseException as error:
  print(json.dumps(refusal(error),sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
