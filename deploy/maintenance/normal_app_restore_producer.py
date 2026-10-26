"""Ordinary isolated restore IO. No protected input is read on import.

This host-side producer keeps normal Config/Env/backup data in operational RAM.
Only its explicit normal check/apply path may consume those protected inputs.
"""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path,PurePosixPath
from types import ModuleType
from urllib.parse import quote,unquote,urlsplit
import argparse,ast,gzip,http.client,io,ipaddress,json,os,re,resource,select,socket,stat,sys,tempfile,time

OP='normal-app-isolated-restore-20261026-02'
REFUSAL_CODES=frozenset(('root_required','unarmed','control','source','source_changed','normal_backup','normal_runtime',
 'clone_target','isolation','isolation_unsupported','owned_identity','mounts','not_stopped','schema','persistence','cleanup','deadline',
 'normal_docker_request','normal_docker_body','normal_docker_refused','normal_docker_unknown','normal_restore_size',
 'normal_redis_persistence','normal_public_output_bound','expiry_refused','expiry_unknown','normal_restore_once',
 'compose_labels','normal_environment','normal_execution','normal_healthcheck','normal_feature_scope','normal_pull_policy',
 'normal_labels','normal_host','normal_network','normal_ports','normal_command_refused','unbound_env_reference'))
HEAD='ff6c7d8e9f01'
ADMISSION_SHA='ecd09f9d4eb1c5a943e8dc3afa764b4d675d836321ec0e2117b1d6d314a72511'
CAPTURE_SHA='a223222cc8bf5221aafa49fd3d69307282dce884b8e998d49fe609c56078e2a0'
UPLOADS_SHA='526f40aae967343b5c0f2a6ac06254a02b8b72076a302ddaf36a3672aca5b69f'
NAMESPACE_SHA='e508fbaa089a268d7d39b38a46105d540965fe583677705a232c85d6be0bdc0e'
DNS_ENGINE_GIT='0367cbeb49b9d98cb0b4f51e95f7b54b4803e76c'
EXTRA_PINS={'backup_postgres_candidate.py':'5187777f471bb367ec4cd16c069e4e4836c29159045af69d1c0f682d3460dabb'}
_RUNTIME=None

class UnixHTTP(http.client.HTTPConnection):
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout)
  self.sock.connect('/var/run/docker.sock')

class Docker:
 """Bound request/response bodies before accumulation; no daemon text escapes."""
 def __init__(self,end):self.end=end
 def request(self,method,path,value=None,*,maximum=4*1024**2,allowed=(200,201,204),binary=False):
  if method not in ('GET','POST','DELETE') or not path.startswith('/') or any(c in path for c in '\r\n'):
   raise RuntimeError('normal_docker_request')
  body=None if value is None else json.dumps(value,separators=(',',':'),allow_nan=False).encode()
  if body is not None and len(body)>2*1024**2:raise RuntimeError('normal_docker_request')
  remaining=self.end-time.time()
  if remaining<=0:raise RuntimeError('deadline')
  client=UnixHTTP('localhost',timeout=min(30,remaining))
  try:
   client.request(method,path,body=body,headers={'Content-Type':'application/json','Connection':'close'})
   response=client.getresponse();length=response.getheader('Content-Length')
   if length is not None and (not length.isdigit() or int(length)>maximum):raise RuntimeError('normal_docker_body')
   chunks=[];count=0
   while True:
    if time.time()>=self.end:raise RuntimeError('deadline')
    part=response.read1(min(65536,maximum-count+1))
    if not part:break
    count+=len(part)
    if count>maximum:raise RuntimeError('normal_docker_body')
    chunks.append(part)
   raw=b''.join(chunks)
   if response.status not in allowed:raise RuntimeError('normal_docker_refused')
   if not raw:return None
   return raw if binary else json.loads(raw)
  except (OSError,http.client.HTTPException,ValueError,TypeError):raise RuntimeError('normal_docker_unknown') from None
  finally:client.close()
 def inspect(self,identifier):
  if not re.fullmatch('[a-f0-9]{64}',identifier):raise RuntimeError('normal_docker_request')
  return self.request('GET','/containers/'+identifier+'/json')
 def image(self,identifier):
  if not re.fullmatch('sha256:[a-f0-9]{64}',identifier):raise RuntimeError('normal_docker_request')
  return self.request('GET','/images/'+quote(identifier,safe='')+'/json')
 def execute(self,identifier,argv,*,environment=()):
  if not re.fullmatch('[a-f0-9]{64}',identifier) or type(argv) is not list or not argv or not all(type(v) is str for v in argv):
   raise RuntimeError('normal_docker_request')
  result=self.request('POST','/containers/'+identifier+'/exec',{'AttachStdout':True,'AttachStderr':True,
   'Tty':False,'Cmd':argv,'Env':list(environment)})
  if type(result) is not dict or not re.fullmatch('[a-f0-9]{64}',result.get('Id','')):raise RuntimeError('normal_docker_unknown')
  raw=self.request('POST','/exec/'+result['Id']+'/start',{'Detach':False,'Tty':False},binary=True)
  out=bytearray();offset=0
  while offset<len(raw or b''):
   if len(raw)-offset<8 or raw[offset] not in (1,2) or raw[offset+1:offset+4]!=b'\0'*3:raise RuntimeError('normal_docker_body')
   number=int.from_bytes(raw[offset+4:offset+8],'big');offset+=8
   if number>len(raw)-offset:raise RuntimeError('normal_docker_body')
   if raw[offset-8]==1:out.extend(raw[offset:offset+number])
   offset+=number
  status=self.request('GET','/exec/'+result['Id']+'/json')
  if status.get('Running') is not False or type(status.get('ExitCode')) is not int:raise RuntimeError('normal_docker_unknown')
  return status['ExitCode'],bytes(out)

def cap_host(row):
 return {'Memory':row['memory'],'MemorySwap':row['memory'],'NanoCpus':row['nano_cpus'],'PidsLimit':row['pids'],
  'LogConfig':{'Type':'json-file','Config':{'max-size':str(row['log_bytes']),'max-file':str(row['log_files'])}},
  'RestartPolicy':{'Name':'no','MaximumRetryCount':0}}
def labels(nonce,role):
 return {'normal.restore.operation':OP,'normal.restore.nonce':nonce,'normal.restore.role':role,
  'trading-bot.production-migration-rehearsal':nonce}
def json_bytes(value):return (json.dumps(value,sort_keys=True,allow_nan=False)+'\n').encode()
def publish(path,value):
 raw=json_bytes(value)
 if len(raw)>131072:raise RuntimeError('normal_public_output_bound')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  os.fchmod(fd,0o600);view=memoryview(raw)
  while view:
   n=os.write(fd,view)
   if n<=0:raise RuntimeError('normal_public_output_bound')
   view=view[n:]
  os.fsync(fd)
 finally:os.close(fd)
 fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:os.fsync(fd)
 finally:os.close(fd)

def normal_api(raw,path,imports,functions):
 """Reuse unchanged normal helper bodies; exclude unused CLI/Git/scratch paths."""
 tree=ast.parse(raw);body=[]
 for node in tree.body:
  if isinstance(node,(ast.Assign,ast.AnnAssign,ast.ClassDef)):body.append(node)
  elif isinstance(node,(ast.Import,ast.ImportFrom)):
   if isinstance(node,ast.ImportFrom) and node.module and (node.module.startswith('alembic') or node.module.startswith('scripts.')):continue
   body.append(node)
  elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in functions:body.append(node)
 name='_normal_restore_api_'+Path(path).stem;module=ModuleType(name);module.__file__=str(path);module.__dict__.update(imports)
 old=sys.modules.get(name);sys.modules[name]=module
 try:exec(compile(ast.fix_missing_locations(ast.Module(body=body,type_ignores=[])),str(path),'exec'),module.__dict__)
 finally:
  if old is None:sys.modules.pop(name,None)
  else:sys.modules[name]=old
 return module

BACKUP_FUNCTIONS={'backup_target_binding_sha256','production_backup_manifest_values'}
REHEARSAL_FUNCTIONS={'_sha256_file','_utc_now','_parse_utc','_require_private_regular_file','_validate_plain_gzip_dump',
 '_validate_tar_gzip','_secure_receipt_file','_result_by_role','_backup_file_items','_recorded_backup_path','_artifact_path',
 '_verify_restore_cleanup_proof','verify_backup_receipt','_quote_identifier','extract_owner_roles','psql','create_owner_roles',
 'restore_plain_dump','_query_public_tables','_invalid_index_count','_current_revision','schema_only_sha256',
 '_inspect_json','_owned_label','_docker_not_found','_require_label_enumeration_empty','cleanup_owned_resources','_require_success'}

class Artifact:
 """Normal authenticated payload: held inode, bounded streaming hash, no names exported."""
 def __init__(self,path,count,pin,stack,*,private_parent=False):
  self.path=Path(path);self.count=count;self.pin=pin
  if type(count) is not int or not 0<count<=1024**4 or not re.fullmatch('[a-f0-9]{64}',pin):raise RuntimeError('normal_backup')
  self.parents=[]
  for p in self.path.parents:
   s=p.lstat()
   if p.resolve(strict=True)!=p or not stat.S_ISDIR(s.st_mode) or s.st_uid or s.st_gid or s.st_mode&0o022:raise RuntimeError('normal_backup')
   if private_parent and p==self.path.parent and stat.S_IMODE(s.st_mode)!=0o700:raise RuntimeError('normal_backup')
   self.parents.append((p,identity(s)[:5]))
  self.before=identity(self.path.lstat())
  if self.before[2:7]!=[0o100600,0,0,1,count]:raise RuntimeError('normal_backup')
  self.fd=os.open(self.path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC);stack.callback(os.close,self.fd)
  self.check();digest=sha256();offset=0
  while offset<count:
   raw=os.pread(self.fd,min(1024**2,count-offset),offset)
   if not raw:raise RuntimeError('normal_backup')
   digest.update(raw);offset+=len(raw)
  if digest.hexdigest()!=pin:raise RuntimeError('normal_backup')
  self.check();stack.callback(self.check)
 def check(self):
  if identity(os.fstat(self.fd))!=self.before or identity(self.path.lstat())!=self.before:raise RuntimeError('normal_backup')
  if any(p.resolve(strict=True)!=p or identity(p.lstat())[:5]!=v for p,v in self.parents):raise RuntimeError('normal_backup')
 def reader(self):
  owner=self
  class Reader:
   def __init__(self):self.offset=0
   def read(self,n):
    owner.check()
    if type(n) is not int or not 0<=n<=1024**2:raise RuntimeError('normal_backup')
    raw=os.pread(owner.fd,n,self.offset);self.offset+=len(raw);return raw
  return Reader()
def identity(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def expanded_count(artifact,maximum,check):
 count=0
 with gzip.GzipFile(fileobj=artifact.reader(),mode='rb') as source:
  while True:
   check();raw=source.read(min(1024**2,maximum-count+1))
   if not raw:break
   count+=len(raw)
   if count>maximum:raise RuntimeError('normal_restore_size')
 artifact.check();return count

def redis_persistence(config):
 """Accept only the normal packaged Redis command; private auth remains in RAM."""
 command=config.get('Cmd');env={r.split('=',1)[0]:r.split('=',1)[1] for r in config.get('Env',[]) if '=' in r}
 if type(command) is not list or command[:1]!=['redis-server'] or (len(command)-1)%2:raise RuntimeError('normal_redis_persistence')
 options={}
 for name,value in zip(command[1::2],command[2::2]):
  if name not in {'--appendonly','--appendfsync','--save','--dir','--dbfilename','--appenddirname','--requirepass'} or name in options:
   raise RuntimeError('normal_redis_persistence')
  options[name]=value
 if options.get('--appendonly')!='yes' or options.get('--dir','/data')!='/data' or options.get('--dbfilename','dump.rdb')!='dump.rdb':
  raise RuntimeError('normal_redis_persistence')
 if options.get('--appenddirname','appendonlydir')!='appendonlydir':raise RuntimeError('normal_redis_persistence')
 return options

def archive_components(original,kind):
 """Only the fixed normal tar roots change; unchanged uploads parser handles bytes."""
 def components(name,directory):
  if kind=='audit' and (name=='audit_trail' or name.startswith('audit_trail/')):name='uploads'+name[len('audit_trail'):]
  if kind=='redis':
   if name.startswith('./'):name=name[2:]
   if directory and name in ('','.'):return ()
   name='uploads/'+name
  return original(name,directory)
 return components

class Runtime:
 def __init__(self,control,stack):
  self.control=control;self.stack=stack;self.end=control['deadline'];self.checks=[];self.current=None;self.persisted=False
  self.started=[];self.resources=None;self.lease=None;self.source_rows={};self.private=None;self.schema=None
  self.C=_restore_contract;self.C.validate_control(control,time.time())
  prefix=self.read_public(control['normal_prefix'])
  expected={'schema','site','machine_id','admission_control','admission_source','normal_backup_manifest_values',
   'postgres_identity_control','uploads_restore_source','namespace_checker_source','normal_support_sources','load_receipt',
   'state_root','subnet','reserve_memory_bytes','maximum_restore_bytes'}
  self.C.need(type(prefix) is dict and set(prefix)==expected and prefix['schema']=='normal_app_restore_inputs/2'
   and prefix['site']==control['site'] and prefix['machine_id']==control['machine_id'],'normal_backup')
  self.prefix=prefix;self.A=self.load(prefix['admission_source'],'admission',ADMISSION_SHA)
  admission=self.read_public(prefix['admission_control']);self.C.need(admission['nonce']==control['nonce'],'control')
  public,self.admission=self.A.collect(admission,stack,ADMISSION_SHA)
  self.C.need(public['site']==control['site'] and public['machine_id']==control['machine_id'],'normal_runtime')
  self.modules={}
  for name,spec in admission['helpers'].items():self.modules[name]=self.load(spec,name,self.A.PINS[name])
  self.S=self.modules['utilities'];self.S.C=self.modules['contract'];self.capture=self.modules['capture'].capture
  self.engine=Docker(self.end);self.snapshots={}
  for v in [*self.admission['datastores'].values(),*(r['actual'] for r in self.admission['roles'].values())]:self.snapshots[v['Id']]=self.A.stable_view(v)
  for role,spec in admission['roles'].items():
   for ref in spec['env_files']:self.checks.append(self.S.OpaqueReference(ref,stack).check)
   for binding in spec['compose_files']:
    self.checks.append(self.S.Held(binding['path'],stack,pin=binding['sha256'],public_source=True).check)
  self.admission_control=admission;self.ro=[]
  self.client_config=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.normal-restore-client-',dir=Path.cwd())))
  self.client_config.chmod(0o700)
 def load(self,spec,name,pin=None):
  if pin is not None:self.C.need(spec['sha256']==pin,'source')
  source=self.A.cold(spec['path'],spec['sha256']) if set(spec)=={'path','sha256'} else self.C.held_source(spec)
  data,check=self.stack.enter_context(source);self.checks.append(check)
  value=ModuleType('_normal_restore_'+name);value.__file__=str(spec['path']);exec(compile(data,value.__file__,'exec'),value.__dict__);return value
 def read_public(self,spec):
  raw,check=self.stack.enter_context(self.C.held_source(spec));self.checks.append(check);value=json.loads(raw);self.C.public(value);return value
 def command(self,argv,timeout=60):
  self.check_sources();self.C.need(resource.getrlimit(resource.RLIMIT_FSIZE)==(resource.RLIM_INFINITY,resource.RLIM_INFINITY),'normal_runtime')
  rc,raw=self.capture(argv,min(timeout,max(.1,self.end-time.time())),environment={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
   'HOME':'/root','DOCKER_HOST':'unix:///var/run/docker.sock','DOCKER_CONFIG':str(self.client_config)})
  return rc,raw
 def check_sources(self):
  self.C.need(time.time()<self.end,'deadline')
  for check in self.checks:check()
 def check(self):
  self.check_sources()
  for identifier,before in self.snapshots.items():self.C.need(self.C.same(before,self.A.stable_view(self.engine.inspect(identifier))),'normal_runtime')
  for row,fd,parents in self.ro:
   self.C.opaque_check(row);current=self.C.identity(os.fstat(fd))
   if row['Destination'] in self.C.LIVE_MODEL_TARGETS:self.C.need(current[:5]==row['identity'][:5] and current[5]>0,'mounts')
   else:self.C.need(current==row['identity'],'mounts')
   self.S.check_parents(parents)
 def capture_readonly_references(self):
  self.ro_by_target={}
  for row in self.admission['roles']['app']['actual']['Mounts']:
   target=row['Destination']
   if target in ('/app/uploads','/app/audit_trail'):continue
   self.C.need(row['Type']=='bind' and target in self.C.DATA_TARGETS and (row['RW'] is False or target=='/app/certs'),'mounts')
   path=Path(row['Source']);before=identity(path.lstat());reference={'Type':'bind','Source':str(path),'Destination':target,'RW':False,'identity':before}
   # Full9 admission is mandatory even for a subsequently live data directory.
   self.C.opaque_check(reference,initial=True);parents=self.S.parents(path)
   fd=os.open(path,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);self.stack.callback(os.close,fd)
   self.C.need(identity(os.fstat(fd))==before==identity(path.lstat()),'mounts');self.S.check_parents(parents)
   self.ro.append((reference,fd,parents));self.ro_by_target[target]=reference
 def admit(self):
  self.C.need(Path.cwd().resolve()==Path(self.admission_control['roles']['app']['workdir']).resolve(),'normal_runtime')
  app=self.admission['roles']['app'];self.C.need(app['actual']['Image']==self.control['old_image_id'],'normal_runtime')
  self.private_images={'old':app['image'],'candidate':self.engine.image(self.control['candidate_image_id'])}
  load=self.read_public(self.prefix['load_receipt'])
  local=self.modules['contract'].host_load(load,self.control['site'],{'machine_id':self.control['machine_id']})
  self.C.need(local==self.control['candidate_image_id'],'normal_runtime')
  self.C.candidate_association(self.private_images['candidate'],local,load)
  self.C.need(type(self.prefix['reserve_memory_bytes']) is int and self.prefix['reserve_memory_bytes']>=256*1024**2
   and type(self.prefix['maximum_restore_bytes']) is int and 1024**2<=self.prefix['maximum_restore_bytes']<=32*1024**3,'normal_backup')
  self.support=self.read_public(self.control['isolation_support']);self.C.isolation_support(self.support,self.control)
  capability=self.read_public(self.control['bridge_capability']);self.C.bridge_capability(capability,self.control)
  self.C.need(os.uname().release==capability['kernel_release'],'isolation_unsupported')
  nft=self.require_command(['/usr/sbin/nft','--version'],10)
  matched=re.fullmatch(rb'nftables v(1\.[0-9]+\.[0-9]+)(?: [^\r\n]{0,100})?\n?',nft)
  self.C.need(matched is not None and matched[1].decode()==capability['nft_version'],'isolation_unsupported')
  version=self.engine.request('GET','/version')
  actual_commit=version.get('GitCommit')
  self.C.need(type(actual_commit) is str and 7<=len(actual_commit)<=40 and DNS_ENGINE_GIT.startswith(actual_commit)
   and actual_commit==self.support['docker_git_commit'],'isolation_unsupported')
  required=set(EXTRA_PINS)
  self.C.need(type(self.prefix['normal_support_sources']) is dict and set(self.prefix['normal_support_sources'])==required,'source')
  extra={name:self.load(self.prefix['normal_support_sources'][name],name,EXTRA_PINS[name]) for name in required}
  self.pg=extra['backup_postgres_candidate.py']
  bspec=self.control['normal_api_sources']['scripts/run_production_backup.py'];rspec=self.control['normal_api_sources']['scripts/rehearse_production_migration.py']
  braw,_=self.stack.enter_context(self.C.held_source(bspec));rraw,_=self.stack.enter_context(self.C.held_source(rspec))
  self.B=normal_api(braw,bspec['path'],{},BACKUP_FUNCTIONS)
  self.R=normal_api(rraw,rspec['path'],{k:getattr(self.B,k) for k in ('DEFAULT_BACKUP_DIR','DEFAULT_BACKUP_RECEIPT_DIR','DEFAULT_IRAN_PULL_DIR',*BACKUP_FUNCTIONS)},REHEARSAL_FUNCTIONS)
  self.R._docker=lambda *args,timeout=60:self.normal_result(['/usr/bin/docker','--host=unix:///var/run/docker.sock',*args],timeout)
  self.R.run_command=lambda argv,timeout=60:self.normal_result(argv,timeout)
  self.U=self.load(self.prefix['uploads_restore_source'],'uploads',UPLOADS_SHA)
  self.C.need(self.prefix['namespace_checker_source']['sha256']==NAMESPACE_SHA
   and Path(self.prefix['namespace_checker_source']['path']).name=='normal_restore_namespace_check.py','source')
  self.namespace_source,self.namespace_check=self.stack.enter_context(self.C.held_source(self.prefix['namespace_checker_source']))
  self.checks.append(self.namespace_check)
  env=self.C.env_map(app['actual']['Config']['Env']);release=env.get('RELEASE_SHA')
  receipt=self.control['backup_receipt'];receipt_file=Artifact(receipt['path'],receipt['bytes'],receipt['sha256'],self.stack,private_parent=True)
  self.checks.append(receipt_file.check);raw=os.pread(receipt_file.fd,receipt['bytes']+1,0);receipt_file.check()
  verified=self.R.verify_backup_receipt(receipt_path=Path(receipt['path']),receipt_sha256=receipt['sha256'],expected_release_sha=release,
   manifest_values=self.prefix['normal_backup_manifest_values'],max_age_seconds=14400,expected_source_head=HEAD)
  self.C.need(verified.pre_migration_head==HEAD,'schema');role='foreign' if self.control['site']=='bot' else 'iran'
  payload=json.loads(raw);result=self.R._result_by_role(payload)[role]
  items=self.R._backup_file_items(result);self.artifacts={}
  for kind,item in items.items():
   path=self.R._artifact_path(role,result,item);self.artifacts[kind]=Artifact(path,item['bytes'],item['sha256'],self.stack)
   self.checks.append(self.artifacts[kind].check)
  self.expanded_bytes=sum(expanded_count(artifact,self.prefix['maximum_restore_bytes'],self.check_sources) for artifact in self.artifacts.values())
  self.C.need(self.expanded_bytes<=self.prefix['maximum_restore_bytes'],'normal_backup')
  pg_control=self.prefix['postgres_identity_control'];self.pg_candidate=self.stack.enter_context(self.pg.bound(Path(pg_control['path']),pg_control['sha256'],role))
  self.pg_candidate.image_check(query=lambda argv:self.public_query(argv))
  self.C.need(self.engine.image(self.pg_candidate.image)['RootFS']['Layers']==self.pg.LAYERS,'normal_backup')
  db=self.admission['datastores']['db'];self.db_env=self.C.env_map(db['Config']['Env'])
  roots=[row['Source'] for row in db['Mounts'] if row['Destination']=='/var/lib/postgresql/data']
  self.C.need(len(roots)==1,'normal_backup');metadata=Path(roots[0]).lstat()
  self.C.need(stat.S_ISDIR(metadata.st_mode) and (metadata.st_uid,metadata.st_gid)==(70,70),'normal_backup')
  self.pg_data_owner=(metadata.st_uid,metadata.st_gid)
  self.user=self.db_env.get('POSTGRES_USER');self.database=self.db_env.get('POSTGRES_DB');password=self.db_env.get('POSTGRES_PASSWORD')
  self.C.need(all(type(v) is str and v for v in (self.user,self.database,password)),'clone_target')
  for name in ('DATABASE_URL','SYNC_DATABASE_URL'):
   value=urlsplit(env[name]);self.C.need(unquote(value.username or '')==self.user and unquote(value.password or '')==password and unquote(value.path)=='/'+self.database,'clone_target')
  self.redis_options=redis_persistence(self.admission['datastores']['redis']['Config'])
  self.redis_url=urlsplit(env['REDIS_URL']);self.redis_auth=self.redis_options.get('--requirepass','')
  self.C.need(unquote(self.redis_url.password or '')==self.redis_auth and self.redis_url.scheme=='redis','clone_target')
  self.redis_old=self.engine.image(self.admission['datastores']['redis']['Image'])
  self.C.need(not self.redis_url.username or self.redis_url.username=='default','clone_target')
  self.network=ipaddress.ip_network(self.prefix['subnet'],strict=True)
  self.C.need(self.network.version==4 and self.network.is_private and self.network.prefixlen==28,'isolation')
  self.state_root=Path(self.prefix['state_root'])
  self.C.need(self.state_root.is_absolute() and '..' not in self.state_root.parts and self.state_root.name=='normal-app-restore','source')
  parent=self.state_root.parent
  self.C.need(parent.resolve(strict=True)==parent and parent.stat().st_uid==parent.stat().st_gid==0 and not parent.stat().st_mode&0o022,'source')
  normal={}
  for label,image_id in (('candidate',self.control['candidate_image_id']),('old',self.control['old_image_id'])):
   normal[label]={k:deepcopy(app['actual'][k]) for k in ('Config','HostConfig','Mounts')}
   normal[label]['Config']['Image']=image_id
   defaults=self.private_images[label]['Config']
   self.C.need(set(self.C.env_map(defaults.get('Env',[])))<=set(self.C.env_map(normal[label]['Config']['Env'])),'normal_runtime')
   normal[label]['Config']['Labels']={**(defaults.get('Labels') or {}),**normal[label]['Config']['Labels']}
   for key in self.modules['contract'].PROVENANCE_LABELS:
    if key in (defaults.get('Labels') or {}):normal[label]['Config']['Labels'][key]=defaults['Labels'][key]
   # Runtime Config carries the exact normal Env/Cmd/Entrypoint, including all
   # feature flags. Only owned resource labels differ in this private plan.
   normal[label]['Config']['Labels'].update(labels(self.control['nonce'],'app_'+label))
   for key,value in (('com.docker.compose.project','nar_'+self.control['nonce']),('com.docker.compose.image',image_id)):
    if key in normal[label]['Config']['Labels']:normal[label]['Config']['Labels'][key]=value
  self.normal=normal
  self.role_scope=[self.C.normal_role_scope(role,row['actual']['Config'],row['actual']['Mounts'],
   {k:{name:'CLONE' for name in self.admission['aliases'][k]} for k in ('db','redis')}) for role,row in self.admission['roles'].items()]
  self.capture_readonly_references()
  self.check()
  return {'normal':normal,'head':HEAD,'role_scope':self.role_scope,'mount_artifacts':{
   r['Destination']:self.artifacts['uploads' if r['Destination']=='/app/uploads' else 'audit'].pin
   for r in app['actual']['Mounts'] if r['Destination'] in ('/app/uploads','/app/audit_trail')},
   'backup':{'receipt_sha256':receipt['sha256'],'durable':True,'prior_database_restore_passed':True,
    'uploads_artifact_authenticated':True,'redis_restore_contract':'normal_persistence_format_authenticated',
    'artifacts':{k:{'sha256':v.pin,'bytes':v.count} for k,v in self.artifacts.items()}}}
 def normal_result(self,argv,timeout):
  rc,raw=self.command(argv,timeout);return self.R.CommandResult(rc,raw.decode('utf-8','strict'),'')
 def public_query(self,argv):
  self.C.need(argv[:2]==['/usr/bin/docker','--host=unix:///var/run/docker.sock'],'normal_backup')
  rc,raw=self.command(argv,30)
  self.C.need(rc==0 and len(raw)<=65536,'normal_backup');return raw.decode().strip()
 def require_command(self,argv,timeout=60):
  rc,raw=self.command(argv,timeout);self.C.need(rc==0,'unknown_outcome');return raw
 def event(self,step):
  self.check_sources();publish(self.run_root/(str(len(self.events)).zfill(3)+'-'+step+'.json'),{
   'operation':OP,'nonce':self.control['nonce'],'step':step,'observed_at':int(time.time()),'security_accepted':False})
  self.events.append(step)
 def owned(self,value,role):
  self.C.need(type(value) is dict and all(value.get('Config',{}).get('Labels',{}).get(k)==v for k,v in labels(self.control['nonce'],role).items()),'owned_identity')
  self.C.need(value.get('Image')==self.images[role],'owned_identity');return value
 def endpoint(self,row,*,stopped=False):
  networks=row['NetworkSettings']['Networks'];self.C.need(type(networks) is dict and len(networks)==1,'isolation')
  endpoint=next(iter(networks.values()));self.C.need(type(endpoint) is dict,'isolation')
  if stopped and endpoint.get('NetworkID')=='':
   self.C.need(row['State']['Running'] is False and row['State']['Pid']==0 and row['State']['StartedAt']=='0001-01-01T00:00:00Z'
    and row['RestartCount']==0 and row['HostConfig']['NetworkMode']==self.lease['network_id'],'isolation')
   self.C.need(next(iter(networks)) in (self.lease['network_id'],'nar_'+self.control['nonce']+'_network'),'isolation')
   for key in ('EndpointID','MacAddress','Gateway','IPAddress','IPv6Gateway','GlobalIPv6Address'):
    self.C.need(endpoint.get(key,'')=='','isolation')
   for key in ('IPPrefixLen','GlobalIPv6PrefixLen'):self.C.need(type(endpoint.get(key,0)) is int and endpoint.get(key,0)==0,'isolation')
   self.C.need(endpoint.get('DNSNames') in (None,[]),'isolation')
  else:self.C.need(endpoint.get('NetworkID')==self.lease['network_id'],'isolation')
  return endpoint
 def create(self,role,image_id,config,host,*,aliases=(),address=None):
  self.check();self.images[role]=image_id;name='nar_'+self.control['nonce']+'_'+role
  body=deepcopy(config);body['Image']=image_id;body['Labels']={**body.get('Labels',{}),**labels(self.control['nonce'],role)}
  for key,value in (('com.docker.compose.project','nar_'+self.control['nonce']),('com.docker.compose.image',image_id)):
   if key in body['Labels']:body['Labels'][key]=value
  body['HostConfig']=deepcopy(host)
  endpoint={'Aliases':list(aliases)}
  if address:endpoint['IPAMConfig']={'IPv4Address':address}
  body['NetworkingConfig']={'EndpointsConfig':{self.lease['network_id']:endpoint}}
  self.event('create_'+role+'_intent');result=self.engine.request('POST','/containers/create?name='+name,body)
  self.C.need(type(result) is dict and self.C.cid(result.get('Id')),'unknown_outcome')
  identifier=result['Id'];self.container_ids.append(identifier);self.container_roles[identifier]=role
  row=self.owned(self.engine.inspect(identifier),role)
  self.C.need(row['State']['Running'] is False and row['State']['Pid']==0,'not_stopped');self.endpoint(row,stopped=True)
  if role in ('db','redis','redis_rdb','redis_old'):
   store='db' if role=='db' else 'redis';target='/var/lib/postgresql/data' if store=='db' else '/data'
   wanted={'Type':'volume','Name':self.lease['volumes'][store],'Destination':target,'RW':True}
   self.C.need(type(row['Mounts']) is list and len(row['Mounts'])==1 and all(self.C.same(row['Mounts'][0].get(k),v) for k,v in wanted.items())
    and Path(row['Mounts'][0]['Source'])==self.volume_paths[store][1],'mounts')
   self.C.need(not row['HostConfig'].get('Binds') and self.C.same(self.C.host_mounts(row['HostConfig'].get('Mounts')),self.C.host_mounts(host['Mounts'])),'mounts')
   for key in ('Env','Cmd','Entrypoint','User','WorkingDir'):
    self.C.need(self.C.same(row['Config'].get(key),body.get(key)),'normal_runtime')
  self.event('create_'+role+'_complete');return identifier
 def start(self,identifier):
  role=self.container_roles[identifier];self.owned(self.engine.inspect(identifier),role)
  self.event('start_'+role+'_intent');self.engine.request('POST','/containers/'+identifier+'/start')
  self.started.append(identifier);self.event('start_'+role+'_returned')
 def stop(self,identifier):
  role=self.container_roles[identifier];row=self.owned(self.engine.inspect(identifier),role)
  self.C.need(row['State']['Running'] is True and type(row['State']['Pid']) is int and row['State']['Pid']>0,'not_stopped')
  # This is the unchanged normal generic cgroup body, without App.Site construction.
  empty=self.S.Site.cgroup(self,self.S.row(row))
  self.event('stop_'+role+'_intent');self.engine.request('POST','/containers/'+identifier+'/stop?t=30')
  after=self.owned(self.engine.inspect(identifier),role)
  self.C.need(after['State']['Running'] is False and after['State']['Pid']==0,'not_stopped');empty()
  self.event('stop_'+role+'_complete');return after
 def remove(self,identifier):
  row=self.owned(self.engine.inspect(identifier),self.container_roles[identifier])
  self.C.need(row['State']['Running'] is False and row['State']['Pid']==0,'not_stopped')
  self.event('remove_'+self.container_roles[identifier]+'_intent');self.engine.request('DELETE','/containers/'+identifier)
  self.removed.add(identifier)
 def volume(self,role):
  name='nar_'+self.control['nonce']+'_'+role
  self.event('volume_'+role+'_intent');value=self.engine.request('POST','/volumes/create',{'Name':name,'Driver':'local','Labels':labels(self.control['nonce'],role)})
  self.C.need(value.get('Name')==name and value.get('Driver')=='local' and value.get('Options') in ({},None)
   and self.C.same(value.get('Labels'),labels(self.control['nonce'],role)),'owned_identity')
  root=Path(self.engine.request('GET','/info')['DockerRootDir']);path=Path(value['Mountpoint'])
  self.C.need(root.is_absolute() and root.resolve(strict=True)==root and path==root/'volumes'/name/'_data'
   and path.resolve(strict=True)==path,'owned_identity')
  fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);self.stack.callback(os.close,fd)
  before=identity(os.fstat(fd));self.C.need(stat.S_ISDIR(before[2]),'owned_identity')
  self.volume_paths[role]=(name,path,fd,before[:5]);return name
 def restore_tar(self,kind):
  artifact=self.artifacts[kind];rootfd=self.volume_paths[kind][2]
  module=ModuleType('_normal_'+kind+'_restore');module.__dict__.update(self.U.__dict__)
  # Rebind the original function's globals, retaining its bytecode unchanged.
  import types
  module.components=archive_components(self.U.components,kind)
  restore=types.FunctionType(self.U.restore.__code__,module.__dict__,self.U.restore.__name__,self.U.restore.__defaults__,self.U.restore.__closure__)
  restore.__kwdefaults__=self.U.restore.__kwdefaults__
  result=restore(artifact.reader(),rootfd,compressed_bytes=artifact.count,compressed_sha256=artifact.pin,
   maximum_uncompressed=self.prefix['maximum_restore_bytes'],maximum_members=200000,check=self.check_sources)
  name,path,fd,before=self.volume_paths[kind];after=identity(os.fstat(fd))
  self.C.need(after[:2]==before[:2] and identity(path.lstat())[:5]==after[:5],'owned_identity')
  # The authenticated normal archive supplies its root owner/mode. Record this
  # explicit owned-restore transition; every later runtime check is exact.
  self.volume_paths[kind]=(name,path,fd,after[:5]);return result
 def foreign_rules(self):
  raw=self.require_command(['/usr/sbin/nft','--json','--stateless','list','ruleset']);value=json.loads(raw)
  self.C.need(type(value) is dict and set(value)=={'nftables'} and type(value['nftables']) is list,'isolation')
  rows=[];table='nar_'+self.control['nonce']
  for row in value['nftables']:
   self.C.need(type(row) is dict and len(row)==1,'isolation');kind=next(iter(row));v=row[kind]
   if kind=='metainfo':continue
   if type(v) is dict and v.get('family')=='bridge' and ((kind=='table' and v.get('name')==table) or (kind in ('chain','rule') and v.get('table')==table)):continue
   rows.append(row)
  return rows
 def prepare_firewall(self):
  # Native bridge hooks/conntrack; no host-global sysctl/module command or table adoption.
  n=self.control['nonce'];table='nar_'+n;bridge=self.lease['bridge']
  before=self.foreign_rules()
  statements=['create table bridge '+table]
  for name,hook in (('host_input','input'),('isolated_forward','forward'),('host_output','output')):
   statements.append('add chain bridge '+table+' '+name+' { type filter hook '+hook+' priority 0; policy accept; }')
  prefix='add rule bridge '+table+' '
  i='meta ibrname "'+bridge+'" ';o='meta obrname "'+bridge+'" '
  statements.extend([prefix+'host_input '+i+'ether type arp accept',prefix+'host_input '+i+'drop',
   prefix+'isolated_forward '+i+o+'ether type arp accept'])
  for role,port in (('db',5432),('redis',6379)):
   statements.append(prefix+'isolated_forward '+i+o+'ether type ip ip daddr '+self.lease[role]['ip']+' tcp dport '+str(port)+' accept')
  statements.extend([prefix+'isolated_forward '+i+o+'ether type ip ct state established,related accept',
   prefix+'isolated_forward '+i+'drop',prefix+'isolated_forward '+o+'drop',
   prefix+'host_output '+o+'ether type arp accept',prefix+'host_output '+o+'drop'])
  path=self.run_root/'firewall.nft';fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
  try:
   view=memoryview(('\n'.join(statements)+'\n').encode())
   while view:
    count=os.write(fd,view);self.C.need(count>0,'isolation');view=view[count:]
   os.fsync(fd)
  finally:os.close(fd)
  self.event('firewall_intent');self.require_command(['/usr/sbin/nft','--file',str(path)])
  self.rules_before=self.firewall_view();self.C.need(self.C.same(self.foreign_rules(),before),'isolation');self.event('firewall_complete')
 def firewall_view(self):
  raw=self.require_command(['/usr/sbin/nft','--json','list','table','bridge','nar_'+self.control['nonce']])
  value=json.loads(raw);rows=value.get('nftables');self.C.need(type(rows) is list and len(rows)==15,'isolation')
  expected_table='nar_'+self.control['nonce'];chains={};rules=[];tables=0;meta=0
  for row in rows:
   self.C.need(type(row) is dict and len(row)==1,'isolation');kind=next(iter(row));v=row[kind]
   if kind=='metainfo':meta+=1;continue
   self.C.need(kind in ('table','chain','rule') and type(v) is dict and v.get('family')=='bridge','isolation')
   if kind=='table':tables+=1;self.C.need(v.get('name')==expected_table,'isolation');continue
   self.C.need(v.get('table')==expected_table,'isolation')
   if kind=='chain':
    self.C.need(v.get('name') not in chains,'isolation');chains[v['name']]={k:v.get(k) for k in ('type','hook','prio','policy')}
   else:rules.append({k:v.get(k) for k in ('chain','expr')})
  expected={name:{'type':'filter','hook':hook,'prio':0,'policy':'accept'} for name,hook in
   (('host_input','input'),('isolated_forward','forward'),('host_output','output'))}
  self.C.need(tables==meta==1 and self.C.same(chains,expected)
   and self.C.same(rules,self.C.bridge_rules(self.lease['bridge'],self.lease['db']['ip'],self.lease['redis']['ip'])),'isolation')
  return {'chains':chains,'rules':rules}
 def delete_firewall(self):
  self.C.need(self.C.same(self.firewall_view(),self.rules_before),'isolation');foreign=self.foreign_rules()
  self.event('firewall_delete_intent');self.require_command(['/usr/sbin/nft','delete','table','bridge','nar_'+self.control['nonce']])
  self.C.need(self.C.same(self.foreign_rules(),foreign),'cleanup');self.event('firewall_delete_complete')
 def restore(self):
  self.check();self.events=[];self.images={};self.container_ids=[];self.container_roles={};self.removed=set();self.volume_paths={}
  n=self.control['nonce'];self.run_root=self.state_root/n
  if not self.state_root.exists():os.mkdir(self.state_root,0o700)
  self.C.need(self.state_root.resolve(strict=True)==self.state_root and identity(self.state_root.stat())[2:5]==[0o40700,0,0],'source')
  os.mkdir(self.run_root,0o700);self.event('restore_admitted')
  for path in (self.state_root,self.state_root.parent):
   fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
   try:os.fsync(fd)
   finally:os.close(fd)
  # Reject overlap against actual host routes and every Docker network. No
  # caller-selected gateway or preexisting bridge can be adopted.
  for row in self.engine.request('GET','/networks'):
   for item in row.get('IPAM',{}).get('Config') or []:
    if item.get('Subnet'):
     network=ipaddress.ip_network(item['Subnet'],strict=False)
     self.C.need(network.version!=4 or not network.overlaps(self.network),'isolation')
  routes=json.loads(self.require_command(['/usr/sbin/ip','-json','-4','route','show','table','all']))
  self.C.need(type(routes) is list and len(routes)<=4096,'isolation')
  for row in routes:
   if row.get('dst') and row['dst']!='default':self.C.need(not ipaddress.ip_network(row['dst'],strict=False).overlaps(self.network),'isolation')
  mem={}
  with open('/proc/meminfo','rb') as source:raw=source.read(65537)
  self.C.need(len(raw)<=65536,'normal_runtime')
  for line in raw.splitlines():
   if line.startswith(b'MemAvailable:'):mem['available']=int(line.split()[1])*1024
  self.C.need(mem.get('available',0)>=self.prefix['reserve_memory_bytes']+sum(self.control['limits'][k]['memory'] for k in ('api','db','redis')),'normal_runtime')
  data=Path(self.engine.request('GET','/info')['DockerRootDir']);storage=os.statvfs(data)
  self.C.need(storage.f_bavail*storage.f_frsize>=8*1024**3+4*self.expanded_bytes,'normal_runtime')
  self.arm_expiry()
  bridge='nar'+n[:12];name='nar_'+n+'_network'
  self.event('network_intent');network=self.engine.request('POST','/networks/create',{'Name':name,'CheckDuplicate':True,'Driver':'bridge',
   'Internal':True,'EnableIPv6':False,'Attachable':False,'Labels':labels(n,'network'),
   'Options':{'com.docker.network.bridge.name':bridge,'com.docker.network.bridge.enable_icc':'true'},
   'IPAM':{'Driver':'default','Config':[{'Subnet':str(self.network),'Gateway':str(self.network.network_address+1)}]}})
  self.C.need(type(network) is dict and self.C.cid(network.get('Id')),'unknown_outcome')
  self.lease={'nonce':n,'network_id':network['Id'],'bridge':bridge,'subnet':str(self.network),
   'volumes':{},'api_mounts':[],**{role:{'id':None,'ip':str(self.network.network_address+i),'aliases':sorted(self.admission['aliases'][role]),
   'artifact_sha256':self.artifacts[role].pin} for i,role in ((2,'db'),(3,'redis'))}}
  for role in ('db','redis','uploads','audit'):self.lease['volumes'][role]=self.volume(role)
  for row in self.admission['roles']['app']['actual']['Mounts']:
   target=row['Destination']
   if target in ('/app/uploads','/app/audit_trail'):
    kind='uploads' if target=='/app/uploads' else 'audit'
    self.lease['api_mounts'].append({'Type':'volume','Name':self.lease['volumes'][kind],'Destination':target,'RW':row['RW'],'artifact_sha256':self.artifacts[kind].pin})
   else:
    self.C.need(target in self.ro_by_target,'mounts');self.lease['api_mounts'].append(deepcopy(self.ro_by_target[target]))
  self.prepare_firewall()
  for kind in ('uploads','audit'):self.restore_tar(kind)
  self.restore_redis_rdb()
  name,path,fd,before=self.volume_paths['db']
  # The fixed admitted PostgreSQL 15 Alpine image uses uid/gid70. Its ordinary
  # entrypoint requires an owned0700 PGDATA; this is a fresh disposable volume.
  os.fchown(fd,*self.pg_data_owner);os.fchmod(fd,0o700);os.fsync(fd)
  self.volume_paths['db']=(name,path,fd,identity(os.fstat(fd))[:5])
  db_config=deepcopy(self.engine.image(self.pg_candidate.image)['Config'])
  db_config['Env']=deepcopy(self.admission['datastores']['db']['Config']['Env'])
  db_host=self.store_host('db');db=self.create('db',self.pg_candidate.image,db_config,db_host,aliases=self.lease['db']['aliases'],address=self.lease['db']['ip'])
  self.lease['db']['id']=db;self.start(db)
  self.wait_exec(db,['pg_isready','-U',self.user,'-d',self.database],lambda rc,raw:rc==0)
  self.pg_candidate.runtime_check(self.engine.inspect(db),query=self.public_query,restore=True)
  roles=self.R.extract_owner_roles(Path('/proc/'+str(os.getpid())+'/fd/'+str(self.artifacts['db'].fd)))
  self.R.create_owner_roles(db,username=self.user,database=self.database,roles=roles)
  self.R.restore_plain_dump(db,username=self.user,database=self.database,
   dump_path=Path('/proc/'+str(os.getpid())+'/fd/'+str(self.artifacts['db'].fd)),timeout=min(600,int(self.end-time.time())))
  self.C.need(self.revision()==HEAD and self.R._invalid_index_count(db,self.user,self.database)==0,'schema')
  self.schema=self.R.schema_only_sha256(db,self.user,self.database)
  redis=self.create('redis',self.redis_old['Id'],self.admission['datastores']['redis']['Config'],self.store_host('redis'),
   aliases=self.lease['redis']['aliases'],address=self.lease['redis']['ip'])
  self.lease['redis']['id']=redis;self.start(redis);self.wait_redis(redis)
  self.C.need(self.redis_exec(redis,['DBSIZE']).isdigit() and int(self.redis_exec(redis,['DBSIZE']))>0,'persistence')
  self.namespace_probe();self.check();self.event('restore_complete');return deepcopy(self.lease)
 def store_host(self,role):
  return {**cap_host(self.control['limits'][role]),'NetworkMode':self.lease['network_id'],'PortBindings':{},'PublishAllPorts':False,
   'Dns':['127.0.0.1'],'DnsSearch':['.'],'ExtraHosts':[],'Binds':[],
   'Mounts':[{'Type':'volume','Source':self.lease['volumes'][role],'Target':'/var/lib/postgresql/data' if role=='db' else '/data','ReadOnly':False}]}
 def wait_exec(self,identifier,argv,accept):
  end=min(self.end,time.time()+90)
  while True:
   self.check();rc,raw=self.engine.execute(identifier,argv)
   if accept(rc,raw):return raw
   self.C.need(time.time()<end,'normal_runtime');time.sleep(.5)
 def redis_exec(self,identifier,args):
  rc,raw=self.engine.execute(identifier,['redis-cli','--raw',*args],environment=['REDISCLI_AUTH='+self.redis_auth] if self.redis_auth else [])
  self.C.need(rc==0 and len(raw)<=65536,'persistence');return raw.decode('utf-8','strict').strip()
 def wait_redis(self,identifier):
  end=min(self.end,time.time()+90)
  while True:
   self.check()
   if self.redis_exec(identifier,['PING'])=='PONG':return
   self.C.need(time.time()<end,'persistence');time.sleep(.5)
 def revision(self):return self.R._current_revision(self.lease['db']['id'],self.user,self.database)
 def restore_redis_rdb(self):
  # Restore normal archive through the unchanged bounded file parser into an
  # exclusive intermediate owned volume; only the completed RDB is selected.
  self.lease['volumes']['redisstage']=self.volume('redisstage')
  old=self.volume_paths['redis'];self.volume_paths['redis']=self.volume_paths['redisstage']
  try:
   self.restore_tar('redis');self.volume_paths['redisstage']=self.volume_paths['redis']
  finally:self.volume_paths['redis']=old
  stage=self.volume_paths['redisstage'][2];destination=old[2]
  # The parser-owned directory does not excuse leaf admission: another writer
  # or a wrong descriptor must not turn a FIFO, alias or foreign file into IO.
  before=identity(os.stat('dump.rdb',dir_fd=stage,follow_symlinks=False));owner=os.fstat(stage)
  self.C.need(stat.S_ISREG(before[2]) and before[3:5]==[owner.st_uid,owner.st_gid] and before[5]==1
   and 16<=before[6]<=self.prefix['maximum_restore_bytes'],'persistence')
  source=os.open('dump.rdb',os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=stage)
  try:
   self.C.need(identity(os.fstat(source))==before,'persistence')
   target=os.open('dump.rdb',os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=destination)
   try:
    offset=0;expected=sha256()
    while offset<before[6]:
     raw=os.pread(source,min(1024**2,before[6]-offset),offset);self.C.need(bool(raw),'persistence');expected.update(raw);view=memoryview(raw)
     while view:count=os.write(target,view);self.C.need(count>0,'persistence');view=view[count:]
     offset+=len(raw);self.check_sources()
    os.fchown(target,before[3],before[4]);os.fchmod(target,before[2]&0o777);os.fsync(target)
    self.C.need(identity(os.fstat(source))==before==identity(os.stat('dump.rdb',dir_fd=stage,follow_symlinks=False)),'persistence')
    actual=sha256();offset=0
    while offset<before[6]:
     raw=os.pread(target,min(1024**2,before[6]-offset),offset);self.C.need(bool(raw),'persistence');actual.update(raw);offset+=len(raw)
    self.C.need(actual.digest()==expected.digest() and os.fstat(target).st_size==before[6],'persistence')
   finally:os.close(target)
  finally:os.close(source)
  os.fsync(destination)
  restored=os.fstat(stage);os.fchown(destination,restored.st_uid,restored.st_gid);os.fchmod(destination,restored.st_mode&0o777);os.fsync(destination)
  name,path,fd,_=old;self.volume_paths['redis']=(name,path,fd,identity(os.fstat(fd))[:5])
  image=self.redis_old['Id'];config=deepcopy(self.admission['datastores']['redis']['Config'])
  # Normal Redis loader verifies RDB format/checksum. AOF is intentionally
  # absent from the destination, then regenerated by Redis itself.
  config['Cmd']=['redis-server','--appendonly','no',*(['--requirepass',self.redis_auth] if self.redis_auth else [])]
  cid=self.create('redis_rdb',image,config,self.store_host('redis'));self.start(cid);self.wait_redis(cid)
  self.C.need(int(self.redis_exec(cid,['DBSIZE']))>0 and self.redis_exec(cid,['CONFIG','SET','appendonly','yes'])=='OK','persistence')
  end=min(self.end,time.time()+90)
  while True:
   value=self.redis_exec(cid,['INFO','persistence'])
   fields=dict(line.split(':',1) for line in value.splitlines() if ':' in line)
   if fields.get('aof_enabled')=='1' and fields.get('aof_rewrite_in_progress')=='0' and fields.get('aof_last_bgrewrite_status')=='ok':break
   self.C.need(time.time()<end,'persistence');time.sleep(.5)
  self.stop(cid);self.remove(cid)
 def arm_expiry(self):
  expires=self.control['issued_at']+self.control['limits']['lifetime_seconds']
  self.C.need(time.time()<expires<=self.end,'deadline')
  record={'schema':'normal_restore_expiry/1','operation':OP,'nonce':self.control['nonce'],'machine_id':self.control['machine_id'],
   'deadline':expires,'producer':self.control['producer'],'images':{'db':self.pg_candidate.image,'redis':self.redis_old['Id'],
    'redis_rdb':self.redis_old['Id'],'redis_old':self.redis_old['Id'],'app_candidate':self.control['candidate_image_id'],
    'app_old':self.control['old_image_id']}}
  self.expiry_file=self.run_root/'expiry.json';publish(self.expiry_file,record)
  self.expiry_pin=sha256(json_bytes(record)).hexdigest();self.timer='nar-expire-'+self.control['nonce']+'.timer'
  self.expiry_argv=['/usr/bin/python3','-I','-B',self.control['producer']['path'],'--expire',str(self.expiry_file),
   '--expiry-sha256',self.expiry_pin,'--source-sha256',self.control['producer']['sha256']]
  self.require_command(['/usr/bin/systemd-run','--unit',self.timer[:-6],'--on-calendar=@'+str(expires),
   '--timer-property=AccuracySec=1s','--property=RuntimeMaxSec=240','--property=TimeoutStopSec=2','--property=KillMode=control-group',
   '--property=SendSIGKILL=yes','--property=MemoryMax=128M','--property=MemorySwapMax=0','--property=TasksMax=32',
   '--property=CPUQuota=100%',*self.expiry_argv])
  self.expiry_view()
 def expiry_view(self):
  raw=self.require_command(['/usr/bin/systemctl','show',self.timer,'--property=LoadState,ActiveState,Unit'])
  rows=dict(line.split('=',1) for line in raw.decode().splitlines() if '=' in line)
  self.C.need(rows=={'LoadState':'loaded','ActiveState':'active','Unit':self.timer[:-6]+'.service'},'deadline')
  raw=self.require_command(['/usr/bin/systemctl','show',self.timer[:-6]+'.service','--property=ExecStart'])
  # Every argv scalar is fixed public input. The raw D-Bus rendering stays RAM.
  value=raw.decode();self.C.need(all(arg in value for arg in self.expiry_argv) and value.count('path=')==1,'deadline')
  self.C.need(time.time()<self.control['issued_at']+self.control['limits']['lifetime_seconds'],'deadline')
 def namespace_probe(self):
  # Independent HOST stdlib tool, only in the already-owned Redis net namespace.
  # The App image, profile, Env, entrypoint, command and healthcheck are untouched.
  identifier=self.lease['redis']['id'];before=self.owned(self.engine.inspect(identifier),'redis')
  state=before['State'];pid=state['Pid'];self.C.need(state['Running'] is True and type(pid) is int and pid>0,'isolation')
  self.C.need(not state['Restarting'] and not state['Paused'] and not state['Dead'],'isolation')
  self.S.Site.cgroup(self,self.S.row(before))  # bind the PID to this exact owned CID, no stop/kill
  pidfd=None;namespace=None
  try:
   pidfd=os.pidfd_open(pid,0)
   name='/proc/'+str(pid)+'/ns/net';link=os.readlink(name)
   self.C.need(re.fullmatch(r'net:\[[0-9]+\]',link) and link!=os.readlink('/proc/self/ns/net'),'isolation')
   namespace=os.open(name,os.O_RDONLY|os.O_CLOEXEC)
   held=identity(os.fstat(namespace));self.C.need(held[1]==int(link[5:-1]),'isolation')
   def unchanged():
    self.check();self.namespace_check()
    self.C.need(not select.select([pidfd],[],[],0)[0] and identity(os.fstat(namespace))==held and os.readlink(name)==link,'isolation')
    self.C.need(self.C.same(self.A.stable_view(before),self.A.stable_view(self.engine.inspect(identifier))),'isolation')
   unchanged()
   expected={'db':self.lease['db']['ip'],'redis':self.lease['redis']['ip']}
   self.C.need(all(role in self.lease[role]['aliases'] for role in expected),'isolation')
   # Executed bytes come from the retained source buffer. Child argv contains
   # only public fixed aliases/IPs and a descriptor path, never normal Env/URLs.
   code='exec(compile(bytes.fromhex('+repr(self.namespace_source.hex())+'),"owned_network_checker","exec"))'
   argv=['/usr/bin/nsenter','--net=/proc/'+str(os.getpid())+'/fd/'+str(namespace),'--',
    '/usr/bin/python3','-I','-B','-c',code,json.dumps(expected,separators=(',',':')),str(self.network.network_address+1)]
   rc,raw=self.command(argv,20);unchanged()
   self.C.need(type(rc) is int and rc==0 and type(raw) is bytes and len(raw)<=4096 and self.C.same(json.loads(raw),{'schema':'normal_owned_namespace_check/1',
    'clone_dns_and_tcp_passed':True,'reserved_dns_name_not_resolved':True,'fixed_ipv4_witnesses_blocked':True,
    'fixed_ipv6_witness_blocked':True,'security_accepted':False}),'isolation')
   self.dns_verified=True
  finally:
   primary=sys.exc_info()[0] is not None;cleanup_failed=False
   for fd in (namespace,pidfd):
    if fd is not None:
     try:os.close(fd)
     except OSError:cleanup_failed=True
   if cleanup_failed and not primary:raise self.C.Refused('isolation')
 def isolation(self):
  self.check();self.expiry_view();self.C.need(self.dns_verified,'isolation')
  network=self.engine.request('GET','/networks/'+self.lease['network_id'])
  self.C.need(network.get('Internal') is True and network.get('Driver')=='bridge' and network.get('EnableIPv6') is False
   and self.C.same(network.get('Labels'),labels(self.control['nonce'],'network'))
   and self.C.same(network.get('Options'),{'com.docker.network.bridge.name':self.lease['bridge'],'com.docker.network.bridge.enable_icc':'true'})
   and network.get('IPAM',{}).get('Driver')=='default'
   and self.C.same(network.get('IPAM',{}).get('Config'),[{'Subnet':self.lease['subnet'],'Gateway':str(self.network.network_address+1)}]),'isolation')
  expected=set(self.container_ids)-self.removed;attached=set()
  stores={};actual_aliases={}
  for cid in expected:
   role=self.container_roles[cid];row=self.owned(self.engine.inspect(cid),role);host=row['HostConfig'];endpoint=self.endpoint(row,stopped=True)
   if endpoint['NetworkID']:attached.add(cid)
   self.C.need(not any(row['NetworkSettings'].get('Ports',{}).values())
    and host['NetworkMode']==self.lease['network_id'] and not host.get('PortBindings') and not host.get('PublishAllPorts')
    and not host.get('Privileged') and host.get('PidMode','')=='' and host.get('IpcMode') in ('private','')
    and not host.get('Devices') and host.get('Dns')==['127.0.0.1'] and host.get('DnsSearch')==['.'],'isolation')
   limit=self.control['limits']['api' if role.startswith('app_') else role]
   self.C.need(all(self.C.same(host.get(k),v) for k,v in cap_host(limit).items()),'normal_runtime')
   if role in ('db','redis'):
    self.C.need(endpoint['IPAddress']==self.lease[role]['ip'] and set(self.lease[role]['aliases'])<=set(endpoint.get('Aliases') or []),'isolation')
    stores[role]={'id':cid,'operation':OP,'nonce':self.control['nonce'],'role':role,'network_id':self.lease['network_id'],'volume':self.lease['volumes'][role]}
    actual_aliases[role]={name:endpoint['IPAddress'] for name in self.lease[role]['aliases']}
  self.C.need(set(network.get('Containers',{}))==attached,'isolation')
  for role,(name,path,fd,before) in self.volume_paths.items():
   row=self.engine.request('GET','/volumes/'+name)
   self.C.need(row['Name']==name and self.C.same(row.get('Labels'),labels(self.control['nonce'],role))
    and Path(row['Mountpoint'])==path and path.resolve(strict=True)==path
    and identity(os.fstat(fd))[:5]==before[:5]==identity(path.lstat())[:5],'owned_identity')
  rules=self.firewall_view();self.C.need(self.C.same(rules,self.rules_before),'isolation')
  return {'schema':'normal_restore_network_isolation/2','nonce':self.control['nonce'],'network_id':self.lease['network_id'],
   'internal':True,'other_networks':[],'published_ports':[],'host_network':False,'host_pid':False,'host_ipc':False,'privileged':False,'devices':[],
   'dns':['127.0.0.1'],'dns_search':['.'],'dns_forwarding':'verified_container_loopback_only','docker_git_commit':self.support['docker_git_commit'],
   'support_sha256':self.control['isolation_support']['sha256'],'bridge_capability_sha256':self.control['bridge_capability']['sha256'],
   'bridge_policy_sha256':self.C.BRIDGE_POLICY_SHA,'aliases':actual_aliases,'stores':stores,
   'volume_owners':{name:{'operation':OP,'nonce':self.control['nonce'],'role':role} for role,name in self.lease['volumes'].items()},
   'expiry':{'timer':self.timer,'active':True,'producer_sha256':self.control['producer']['sha256'],
    'deadline':self.control['issued_at']+self.control['limits']['lifetime_seconds']},
   'rules':self.C.bridge_summary(self.control['nonce'],self.lease)}

 def create_api(self,label,image_id):
  self.C.need(self.current is None and label in ('candidate','old'),'owned_identity');self.isolation()
  normal=self.normal[label];host=deepcopy(normal['HostConfig'])
  host.update(cap_host(self.control['limits']['api']));host.update(NetworkMode=self.lease['network_id'],PortBindings={},PublishAllPorts=False,
   Dns=['127.0.0.1'],DnsSearch=['.'],ExtraHosts=[],Binds=None,
   Mounts=[{'Type':r['Type'],'Source':r.get('Name',r.get('Source')),'Target':r['Destination'],'ReadOnly':not r['RW']} for r in self.lease['api_mounts']])
  identifier=self.create('app_'+label,image_id,normal['Config'],host,address=str(self.network.network_address+4))
  self.current=(label,identifier);return identifier
 def inspect_api(self,identifier):
  self.C.need(self.current is not None and self.current[1]==identifier,'owned_identity')
  row=self.owned(self.engine.inspect(identifier),'app_'+self.current[0]);endpoint=self.endpoint(row,stopped=True)
  return {'id':identifier,'image':row['Image'],'Config':row['Config'],'HostConfig':row['HostConfig'],
   'network_id':endpoint['NetworkID'],'running':row['State']['Running'],'pid':row['State']['Pid'],
   'Mounts':[{k:r[k] for k in ('Type','Destination','RW',('Name' if r['Type']=='volume' else 'Source'))} for r in row['Mounts']]}
 def ready_api(self,identifier):
  end=min(self.end,time.time()+120);initial=None
  while True:
   self.check();self.isolation();row=self.owned(self.engine.inspect(identifier),'app_'+self.current[0]);state=row['State']
   self.C.need(state['Running'] is True and not state['Restarting'] and not state['Paused'] and not state['Dead']
    and type(state['Pid']) is int and state['Pid']>0 and row['RestartCount']==0,'normal_runtime')
   lifecycle=(state['Pid'],state['StartedAt'],row['RestartCount'])
   if initial is None:initial=lifecycle
   self.C.need(lifecycle==initial,'normal_runtime')
   rc,raw=self.engine.execute(identifier,['python','-c',"import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/api/config',timeout=3); assert r.status==200"])
   if rc==0 and state.get('Health',{}).get('Status')=='healthy':break
   self.C.need(time.time()<end,'normal_runtime');time.sleep(.5)
  measured=self.inspect_api(identifier)
  self.C.runtime_parity(self.normal[self.current[0]],measured,row['Image'],self.lease,self.control,stopped_required=False)
  return {'id':identifier,'image':row['Image'],'running':True,'pid':state['Pid'],'started':state['StartedAt'],
   'restarts':row['RestartCount'],'normal_api_config_returncode':0}
 def synthetic_count(self):
  return int(self.R.psql(self.lease['db']['id'],username=self.user,database=self.database,
   sql="SELECT count(*) FROM users WHERE account_name='"+self.synthetic_account+"' AND telegram_id="+str(self.synthetic_telegram)+";"))
 def measure_data(self,identifier):
  self.C.need(self.current is not None and self.current[1]==identifier,'owned_identity')
  self.synthetic_account='nar_'+self.control['nonce'][:16];self.synthetic_telegram=8000000000000000+int(self.control['nonce'][:8],16)
  if self.current[0]=='candidate':
   self.C.need(self.synthetic_count()==0,'schema')
   mobile='090'+str(int(self.control['nonce'][:8],16)%100000000).zfill(8)
   # The existing installed management command writes a normal User and its
   # mandatory membership. No source mount, custom Env, SQL write or migration.
   argv=['sh','-c','printf "%s\\n" "$1" "$2" "$3" "$4" | python manage.py create_super_admin','normal-owned-clone',
    'Normal restore synthetic user',self.synthetic_account,mobile,str(self.synthetic_telegram)]
   self.event('candidate_business_write_intent');rc,_=self.engine.execute(identifier,argv)
   self.C.need(rc==0 and self.synthetic_count()==1,'schema');self.event('candidate_business_write_complete')
   self.C.need(self.redis_exec(self.lease['redis']['id'],['SET','normal_restore:'+self.control['nonce'],self.control['nonce']])=='OK','persistence')
  self.C.need(self.synthetic_count()==1 and self.revision()==HEAD and self.R._invalid_index_count(self.lease['db']['id'],self.user,self.database)==0
   and self.R.schema_only_sha256(self.lease['db']['id'],self.user,self.database)==self.schema,'schema')
  return {'head':HEAD,'integrity_passed':True,'normal_startup_completed':True,'clone_writes_retained':True,'persistence_verified':self.persisted}
 def stop_api(self,identifier):
  self.C.need(self.current is not None and self.current[1]==identifier,'owned_identity');self.stop(identifier);self.remove(identifier);self.current=None
  return {'id':identifier,'running':False,'pid':0,'cgroup_populated':0,'descendants':0}
 def restart_stores(self):
  self.C.need(self.current is None and self.synthetic_count()==1,'persistence')
  db=self.lease['db']['id'];redis=self.lease['redis']['id'];self.stop(db);self.stop(redis)
  self.start(db);self.wait_exec(db,['pg_isready','-U',self.user,'-d',self.database],lambda rc,raw:rc==0)
  # The exact old normal Redis image consumes the candidate-written persisted
  # owned data, then stops; the original owned candidate container is restarted.
  old=self.create('redis_old',self.redis_old['Id'],self.admission['datastores']['redis']['Config'],self.store_host('redis'))
  self.start(old);self.wait_redis(old)
  self.C.need(self.redis_exec(old,['GET','normal_restore:'+self.control['nonce']])==self.control['nonce'],'persistence')
  self.stop(old);self.remove(old);self.start(redis);self.wait_redis(redis)
  self.C.need(self.redis_exec(redis,['GET','normal_restore:'+self.control['nonce']])==self.control['nonce'] and self.synthetic_count()==1,'persistence')
  self.persisted=True;self.event('same_owned_data_restart_complete')
 def cleanup(self):
  self.C.need(self.current is None and self.persisted,'cleanup');self.check();self.isolation()
  for cid in reversed(self.container_ids):
   if cid not in self.removed:self.stop(cid);self.remove(cid)
  # Normal cleanup's anonymous-volume policy deliberately refuses named
  # volumes. Keep that policy: named volumes require their own exact labels and
  # measured fresh Mountpoint/FD identity before this scoped removal.
  for role,(name,path,fd,before) in self.volume_paths.items():
   value=self.engine.request('GET','/volumes/'+name)
   self.C.need(self.C.same(value.get('Labels'),labels(self.control['nonce'],role)) and Path(value['Mountpoint'])==path
    and path.resolve(strict=True)==path and identity(os.fstat(fd))[:5]==before==identity(path.lstat())[:5],'owned_identity')
   self.event('remove_volume_'+role+'_intent');self.engine.request('DELETE','/volumes/'+name)
  self.engine.request('DELETE','/networks/'+self.lease['network_id'])
  self.C.need(self.C.same(self.firewall_view(),self.rules_before),'isolation')
  self.delete_firewall()
  self.require_command(['/usr/bin/systemctl','stop',self.timer])
  self.check()
  containers=self.engine.request('GET','/containers/json?all=1&filters='+quote(json.dumps({'label':['normal.restore.nonce='+self.control['nonce']]}),safe=''))
  volumes=self.engine.request('GET','/volumes?filters='+quote(json.dumps({'label':['normal.restore.nonce='+self.control['nonce']]}),safe=''))
  networks=self.engine.request('GET','/networks?filters='+quote(json.dumps({'label':['normal.restore.nonce='+self.control['nonce']]}),safe=''))
  self.C.need(containers==[] and volumes.get('Volumes') in (None,[]) and networks==[],'cleanup')
  rc,raw=self.command(['/usr/sbin/nft','--json','list','tables'])
  self.C.need(rc==0 and all(not (row.get('table',{}).get('name')=='nar_'+self.control['nonce'] and row.get('table',{}).get('family')=='bridge') for row in json.loads(raw)['nftables']),'cleanup')
  self.event('cleanup_complete')
  return {'nonce':self.control['nonce'],'containers_absent':True,'volumes_absent':True,'network_absent':True,'firewall_absent':True,'production_unchanged':True}

def check():
 if _RUNTIME is not None:_RUNTIME.check()
def admit_normal_inputs(control,normal_apis,helpers):
 global _RUNTIME
 if _RUNTIME is not None:raise RuntimeError('normal_restore_once')
 _RUNTIME=Runtime(control,_restore_stack);return _RUNTIME.admit()
def restore_owned(control,private):return _RUNTIME.restore()
def isolation(control,lease):return _RUNTIME.isolation()
def create_api(control,private,lease,label,image_id):return _RUNTIME.create_api(label,image_id)
def inspect_api(control,lease,identifier):return _RUNTIME.inspect_api(identifier)
def start_api(control,lease,identifier):return _RUNTIME.start(identifier)
def ready_api(control,lease,identifier):return _RUNTIME.ready_api(identifier)
def stop_api(control,lease,identifier):return _RUNTIME.stop_api(identifier)
def restart_owned_stores(control,lease):return _RUNTIME.restart_stores()
def measure_data(control,lease,identifier):return _RUNTIME.measure_data(identifier)
def cleanup_owned(control,lease):return _RUNTIME.cleanup()

def cold(path,pin,stack,maximum=131072):
 """Public bootstrap only; metadata is checked before any content read."""
 path=Path(path)
 if not path.is_absolute() or path.resolve(strict=True)!=path or not re.fullmatch('[a-f0-9]{64}',pin):raise RuntimeError('source')
 parents=[]
 for p in path.parents:
  s=p.lstat()
  if p.resolve(strict=True)!=p or not stat.S_ISDIR(s.st_mode) or s.st_uid or s.st_gid or s.st_mode&0o022:raise RuntimeError('source')
  parents.append((p,identity(s)[:5]))
 before=identity(path.lstat())
 if before[2] not in (0o100600,0o100644) or before[3:6]!=[0,0,1] or not 0<before[6]<=maximum:raise RuntimeError('source')
 fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC);stack.callback(os.close,fd)
 if identity(os.fstat(fd))!=before:raise RuntimeError('source')
 raw=os.pread(fd,before[6]+1,0)
 if len(raw)!=before[6] or sha256(raw).hexdigest()!=pin:raise RuntimeError('source')
 def final():
  if before!=identity(os.fstat(fd)) or before!=identity(path.lstat()) or os.pread(fd,before[6]+1,0)!=raw:raise RuntimeError('source_changed')
  if any(p.resolve(strict=True)!=p or identity(p.lstat())[:5]!=v for p,v in parents):raise RuntimeError('source_changed')
 final();stack.callback(final);return raw,final

def expire(path,pin,self_pin,stack):
 raw,check=cold(path,pin,stack);value=json.loads(raw)
 fields={'schema','operation','nonce','machine_id','deadline','producer','images'}
 if type(value) is not dict or set(value)!=fields or value['schema']!='normal_restore_expiry/1' or value['operation']!=OP:raise RuntimeError('expiry_refused')
 if not re.fullmatch('[a-f0-9]{32}',value['nonce']) or type(value['deadline']) is not int or time.time()<value['deadline']:raise RuntimeError('expiry_refused')
 with open('/etc/machine-id','rb') as source:machine=source.read(65).strip().decode()
 if machine!=value['machine_id'] or value['producer']['sha256']!=self_pin or Path(value['producer']['path'])!=Path(__file__):raise RuntimeError('expiry_refused')
 roles={'db','redis','redis_rdb','redis_old','app_candidate','app_old'}
 if set(value['images'])!=roles or any(not re.fullmatch('sha256:[a-f0-9]{64}',v) for v in value['images'].values()):raise RuntimeError('expiry_refused')
 engine=Docker(time.time()+230)
 rows=engine.request('GET','/containers/json?all=1&filters='+quote(json.dumps({'label':['normal.restore.nonce='+value['nonce']]}),safe=''))
 if type(rows) is not list or len(rows)>len(roles):raise RuntimeError('expiry_refused')
 stopped=0
 for row in rows:
  check();actual=engine.inspect(row['Id']);metadata=actual['Config'].get('Labels') or {};role=metadata.get('normal.restore.role')
  if role not in roles or actual['Image']!=value['images'][role] or any(metadata.get(k)!=v for k,v in labels(value['nonce'],role).items()):raise RuntimeError('expiry_refused')
  if actual['State']['Running'] is True:
   engine.request('POST','/containers/'+actual['Id']+'/stop?t=30');after=engine.inspect(actual['Id'])
   if after['State']['Running'] is not False or after['State']['Pid']!=0:raise RuntimeError('expiry_unknown')
   stopped+=1
 check()
 return {'schema':'normal_restore_expiry_observation/1','nonce':value['nonce'],'owned_containers_stop_returned':stopped,
  'cleanup_performed':False,'cgroup_closure_claimed':False,'retry_allowed':False,'security_accepted':False}

def main(argv=None):
 parser=argparse.ArgumentParser();parser.add_argument('--control');parser.add_argument('--control-sha256')
 parser.add_argument('--contract');parser.add_argument('--contract-sha256');parser.add_argument('--source-sha256',required=True)
 parser.add_argument('--apply',action='store_true');parser.add_argument('--confirm');parser.add_argument('--expire');parser.add_argument('--expiry-sha256')
 args=parser.parse_args(argv);result=None
 try:
  if os.geteuid()!=0 or os.getegid()!=0:raise RuntimeError('root_required')
  with ExitStack() as stack:
   _,source_check=cold(Path(__file__).absolute(),args.source_sha256,stack)
   if args.expire:
    if args.apply or args.control or not args.expiry_sha256:raise RuntimeError('control')
    result=expire(args.expire,args.expiry_sha256,args.source_sha256,stack)
   else:
    if not all((args.control,args.control_sha256,args.contract,args.contract_sha256)):raise RuntimeError('unarmed')
    raw,_=cold(args.contract,args.contract_sha256,stack);C=ModuleType('_normal_restore_cli');C.__file__=args.contract;exec(compile(raw,args.contract,'exec'),C.__dict__)
    raw,_=cold(args.control,args.control_sha256,stack);control=json.loads(raw);C.validate_control(control,time.time())
    if control['producer']['sha256']!=args.source_sha256 or Path(control['producer']['path'])!=Path(__file__):raise RuntimeError('source')
    if args.apply:
     if args.confirm!='REHEARSE-ORDINARY-INSTALLED-APP-ON-OWNED-RESTORES':raise RuntimeError('unarmed')
     result=C.execute(control,stack)
    else:
     global _restore_contract,_restore_stack
     _restore_contract=C;_restore_stack=stack
     private=admit_normal_inputs(control,C.NORMAL_APIS,C.BACKUP_HELPERS);check()
     result={'schema':'normal_app_restore_prerequisites/2','operation':OP,'nonce':control['nonce'],'site':control['site'],
      'normal_role_eligibility':private['role_scope'],'backup_authenticated':True,'clone_or_network_created':False,
      'app_started':False,'security_accepted':False}
   if not args.expire:C.public(result)
   source_check()
  print(json.dumps(result,sort_keys=True));return 0 if result.get('schema')!='normal_app_restore_failure/2' else 3
 except Exception as error:
  # Never expose daemon responses, arguments, private Config or receipt values.
  code=error.args[0] if len(error.args)==1 and type(error.args[0]) is str and error.args[0] in REFUSAL_CODES else 'unknown_outcome'
  print(json.dumps({'schema':'normal_app_restore_refusal/1','status':'refused_or_unknown','code':code,'retry_allowed':False,'security_accepted':False},sort_keys=True));return 3

if __name__=='__main__':raise SystemExit(main())
