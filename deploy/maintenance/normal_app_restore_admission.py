"""Read-only normal restore admission. Config/Env stay in trusted process RAM.

No clone, network, firewall, signal, migration or serving operation is provided.
The mutation producer reuses collect() in-process; its private return must never
be serialized, logged, hashed or written to an artifact.
"""
from contextlib import ExitStack,contextmanager
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit,parse_qs
import argparse,json,os,re,stat,tempfile,time

OP='normal-app-isolated-restore-admission-20261025-01'
PINS={'contract':'dace35e283b08cd332c72cf588a8b903e5bed05952b62280e9c85a873ef617ce',
 'utilities':'395c18cdd842876a4037c4135a087a34a757e9c10cfdf98036474c8a74c4b5cf',
 'capture':'a223222cc8bf5221aafa49fd3d69307282dce884b8e998d49fe609c56078e2a0'}
ROLES={'bot':('app','bot','sync_worker'),'web':('app','sync_worker')}
DATA_TARGETS={'/app/uploads','/app/audit_trail','/app/certs','/app/runtime/coin-inference','/app/runtime/product-estimator'}
C_CODES=frozenset(('apply_not_authorized', 'attempt_spent', 'authority_scope', 'authority_window', 'backup_restore_gate', 'business_environment', 'business_labels', 'candidate_identity', 'candidate_restarted', 'candidate_rootfs', 'cgroup_bound', 'cgroup_changed', 'cgroup_identity', 'cgroup_not_empty', 'cgroup_unproven', 'command_bound', 'command_output_changed', 'command_shape', 'complete_config_fields', 'complete_configuration', 'complete_mounts', 'compose_labels', 'compose_prefix', 'compose_size', 'compose_version', 'container_id', 'container_identity', 'container_shape', 'control_scope', 'created_state_unproven', 'deadline', 'default_env_reference', 'docker_version', 'duplicate_json_key', 'duplicate_role', 'engine_version', 'env_alias_changed', 'env_alias_inventory', 'env_alias_parent_changed', 'env_references', 'environment_shape', 'evidence_size', 'fence_binding', 'file_binding', 'finite_scope', 'forward_after_recovery', 'fresh_gate', 'gate_inventory', 'guard_binding', 'guard_busy', 'guard_cleanup', 'healthcheck', 'held_bound', 'held_changed', 'held_content', 'held_identity', 'host_load_gate', 'host_namespace', 'image_default_delta', 'image_defaults', 'image_id', 'image_identity', 'image_labels', 'inventory_shape', 'journal_container', 'journal_service', 'journal_state', 'json_bound', 'labels', 'legacy_owner_policy', 'legacy_units', 'load_gate_missing', 'machine_id', 'metadata_binding', 'mount_model', 'mounts', 'network_policy', 'nonfinite_json', 'normal_api_unready', 'normal_bot_owner', 'normal_command', 'normal_environment', 'normal_execution', 'normal_feature_scope', 'normal_health_unready', 'normal_healthcheck', 'normal_host', 'normal_host_feature', 'normal_hosts', 'normal_labels', 'normal_logging', 'normal_network', 'normal_ports', 'normal_proc_policy', 'normal_pull_policy', 'normal_render', 'normal_render_changed', 'normal_render_delta', 'normal_resource', 'normal_restart', 'normal_sync_unready', 'normal_ulimits', 'original_source_identity', 'other_containers', 'other_inventory', 'output_bound', 'output_type', 'override_write', 'owned_state_unproven', 'parent_admission', 'parent_changed', 'path_binding', 'private_output', 'private_unit_property', 'public_host_identity', 'public_source_identity', 'reader_unit_policy', 'reader_units', 'readiness_runtime_changed', 'recovery_not_admitted', 'recovery_without_guard', 'role_baseline', 'role_identity', 'role_missing', 'role_not_running', 'role_order', 'role_policy', 'role_scope', 'rollback_configuration', 'rollback_environment', 'rollback_state_unproven', 'root_mode', 'root_site_required', 'runtime_changed', 'runtime_delta', 'schema_gate', 'scope_bound', 'site_scope', 'smoke_archive', 'smoke_artifact', 'smoke_binding', 'smoke_cleanup', 'smoke_daemon', 'smoke_inventory', 'smoke_member', 'smoke_presence', 'smoke_result', 'source_alias', 'source_binding', 'source_inventory', 'source_mount', 'stdin_bound', 'stdin_write', 'stopped_candidate', 'stopped_network', 'terminal_not_prepared', 'terminal_state', 'unarmed', 'unarmed_site', 'unbound_env_reference', 'unit_binding', 'unit_policy_changed', 'write_incomplete'))
def need(ok,code):
 if not ok:raise RuntimeError(code)
def identity(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
@contextmanager
def cold(path,pin,maximum=131072):
 path=Path(path);parents=[]
 for p in path.parents:
  s=p.lstat();need(p.resolve(strict=True)==p and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and not s.st_mode&0o022,'public_source')
  parents.append((p,identity(s)[:5]))
 before=identity(path.lstat());need(path.resolve(strict=True)==path and before[2] in (0o100600,0o100644)
  and before[3:6]==[0,0,1] and 0<before[6]<=maximum,'public_source')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  need(before==identity(os.fstat(fd)),'public_source');raw=os.pread(fd,before[6]+1,0)
  need(len(raw)==before[6] and sha256(raw).hexdigest()==pin,'public_source')
  def check():
   need(all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==v for p,v in parents),'source_changed')
   need(before==identity(os.fstat(fd))==identity(path.lstat()) and os.pread(fd,len(raw)+1,0)==raw,'source_changed')
  check();primary=False
  try:yield raw,check
  except BaseException:primary=True;raise
  finally:
   try:check()
   except BaseException:
    if not primary:raise
 finally:os.close(fd)
def module(raw,path):
 m=ModuleType('_normal_restore_admission_'+Path(path).stem);m.__file__=str(path);exec(compile(raw,str(path),'exec'),m.__dict__);return m
def document(raw):
 def unique(rows):
  d={}
  for k,v in rows:need(k not in d,'json');d[k]=v
  return d
 return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'json'))
def controls(v,now):
 fields={'schema','operation','nonce','site','machine_id','issued_at','deadline','helpers','roles','datastores'}
 need(type(v) is dict and set(v)==fields and v['schema']=='normal_app_restore_admission/1' and v['operation']==OP,'control')
 need(type(v['nonce']) is str and re.fullmatch('[a-f0-9]{32}',v['nonce']) and v['site'] in ROLES
  and type(v['machine_id']) is str and re.fullmatch('[a-f0-9]{32}',v['machine_id']),'control')
 need(type(v['issued_at']) is type(v['deadline']) is int and v['issued_at']<=now<v['deadline']<=v['issued_at']+300,'deadline')
 need(type(v['helpers']) is dict and set(v['helpers'])==set(PINS),'control')
 for name,row in v['helpers'].items():
  need(type(row) is dict and set(row)=={'path','sha256'} and row['sha256']==PINS[name] and Path(row['path']).is_absolute(),'control')
 need(type(v['roles']) is dict and set(v['roles'])==set(ROLES[v['site']]),'role_inventory')
 need(type(v['datastores']) is dict and set(v['datastores'])=={'db','redis'}
  and all(type(i) is str and re.fullmatch('[a-f0-9]{64}',i) for i in v['datastores'].values()),'datastore_inventory')
 return v
def inspect_one(raw):
 values=document(raw);need(type(values) is list and len(values)==1 and type(values[0]) is dict,'normal_inspect');return values[0]
def public_compose_source(binding,raw):
 if binding['path'].endswith(('.yml','.yaml')):return
 need(Path(binding['path']).name=='rollback-app.override.json' and binding['bytes']==153,'normal_prefix')
 value=document(raw);need(type(value) is dict and set(value)=={'services'} and type(value['services']) is dict
  and set(value['services'])=={'app'} and type(value['services']['app']) is dict,'normal_prefix')
 app=value['services']['app'];need(set(app)=={'image','pull_policy','restart'} and type(app['image']) is str
  and re.fullmatch('sha256:[a-f0-9]{64}',app['image']) and type(app['pull_policy']) is str and app['pull_policy']=='never'
  and type(app['restart']) is str and re.fullmatch(r'(?:no|always|unless-stopped|on-failure(?::[0-9]+)?)',app['restart']),'normal_prefix')
def row(value):
 return {'id':value['Id'],'name':value['Name'],'image':value['Image'],'config':value['Config'],'host':value['HostConfig'],
  'mounts':value['Mounts'],'networks':value['NetworkSettings']['Networks']}
def stable_view(value):
 state=value['State'];fields={'Running':bool,'Pid':int,'StartedAt':str,'Restarting':bool,'Paused':bool,'Dead':bool}
 need(type(state) is dict and all(type(state.get(k)) is t for k,t in fields.items())
  and state['Running'] is True and state['Pid']>0 and state['Restarting'] is state['Paused'] is state['Dead'] is False
  and type(value.get('RestartCount')) is int and value['RestartCount']>=0,'normal_runtime')
 return {k:value[k] for k in ('Id','Name','Image','Config','HostConfig','Mounts','RestartCount')}|{
  'networks':value['NetworkSettings']['Networks'],'state':{k:state[k] for k in fields}}
def loader_scope(C,role,value,aliases):
 cfg=value['Config'];env=C.environment(cfg['Env']);cmd=cfg.get('Cmd')
 if role=='app':need(type(cmd) is list and len(cmd)==3 and cmd[:2]==['sh','-c']
  and re.fullmatch(r'exec uvicorn main:app --host 0\.0\.0\.0 --port 8000 --workers [1-9][0-9]*',cmd[2]),'fixed_command')
 else:need(C.same(cmd,['python','run_bot.py'] if role=='bot' else ['python','-m','core.sync_worker']),'fixed_command')
 need(cfg.get('Entrypoint') in (None,[]),'fixed_command')
 need(not any(env.get(k) for k in ('LD_PRELOAD','LD_LIBRARY_PATH','PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONUSERBASE',
  'PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGPASSFILE','PGSYSCONFDIR','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')),'loader_environment')
 for key,kind,schemes,port in (('DATABASE_URL','db',{'postgresql+asyncpg'},5432),
  ('SYNC_DATABASE_URL','db',{'postgresql','postgresql+psycopg2'},5432),('REDIS_URL','redis',{'redis','rediss'},6379)):
  try:
   url=urlsplit(env[key]);need(url.scheme in schemes and url.hostname in aliases[kind] and (url.port or port)==port,'endpoint_eligibility')
   need(set(parse_qs(url.query,keep_blank_values=True))<={'sslmode','application_name','connect_timeout','command_timeout'},'driver_plugins')
  except (ValueError,TypeError,KeyError):raise RuntimeError('endpoint_eligibility') from None
 need(env.get('REDIS_HOST','redis') in aliases['redis'] and env.get('REDIS_PORT','6379')=='6379','endpoint_eligibility')
 need(all(type(m) is dict and m.get('Destination') in DATA_TARGETS and m.get('Type') in {'bind','volume'} for m in value['Mounts']),'source_mounts')
 return {'role':role,'normal_command_valid':True,'postgres_drivers_fixed':True,'loader_env_controlled':True,
  'source_mounts_absent':True,'clone_endpoint_eligible':True}

def collect(value,stack,self_pin,*,capture=None):
 """Return (public,private). Callers may emit ONLY the first item."""
 need(os.geteuid()==os.getegid()==0,'root_required');controls(value,time.time());checks=[];modules={}
 _,check=stack.enter_context(cold(Path(__file__),self_pin));checks.append(check)
 for name,spec in value['helpers'].items():
  raw,check=stack.enter_context(cold(spec['path'],PINS[name]));checks.append(check);modules[name]=module(raw,spec['path'])
 C,S,T=modules['contract'],modules['utilities'],modules['capture'];S.C=C
 docker=['/usr/bin/docker','--host=unix:///var/run/docker.sock'];prefixes={}
 for role,spec in value['roles'].items():
  fields={'project','workdir','compose_files','env_files','container_id','environment_file_label','compose_version'}
  need(type(spec) is dict and set(spec)==fields and re.fullmatch('[a-z0-9][a-z0-9_-]{0,99}',spec['project'])
   and Path(spec['workdir']).is_absolute() and type(spec['container_id']) is str and re.fullmatch('[a-f0-9]{64}',spec['container_id'])
   and type(spec['compose_version']) is str and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?',spec['compose_version']),'normal_prefix')
  workdir=Path(spec['workdir']);resolved=workdir.resolve(strict=True)
  need(Path.cwd().resolve(strict=True)==resolved,'normal_cwd');before=identity(workdir.lstat())
  def workdir_check(path=workdir,target=resolved,old=before):
   need(identity(path.lstat())==old and path.resolve(strict=True)==target and Path.cwd().resolve(strict=True)==target,'normal_cwd')
  checks.append(workdir_check)
  refs=spec['env_files'];need(type(refs) is list and 0<len(refs)<=8 and all(type(r) is dict for r in refs)
   and len({r.get('path') for r in refs})==len(refs),'env_references')
  for ref in refs:
   need(set(ref)=={'path','target','identity','aliases','cli'} and type(ref['cli']) is bool,'env_references')
   C.absolute(ref['path']);C.absolute(ref['target']);C.metadata(ref['identity'])
   need(type(ref['aliases']) is list and len(ref['aliases'])<=8,'env_references')
   for alias in ref['aliases']:
    need(type(alias) is dict and set(alias)=={'path','identity'},'env_references');C.absolute(alias['path']);ident=alias['identity']
    need(type(ident) is list and len(ident)==9 and all(type(v) is int for v in ident)
     and ident[2:6]==[0o120777,0,0,1] and 0<ident[6]<=4096,'env_references')
  cli=[r['path'] for r in refs if r['cli']]
  need(cli or any(r['path']==spec['workdir']+'/.env' for r in refs),'default_env_reference')
  need((not cli and spec['environment_file_label'] is None) or (cli and type(spec['environment_file_label']) is str
   and all(',' not in p for p in cli) and spec['environment_file_label']==','.join(cli)),'env_cli_binding')
  need(type(spec['compose_files']) is list and 1<=len(spec['compose_files'])<=24
   and len({b['path'] for b in spec['compose_files']})==len(spec['compose_files']),'normal_prefix')
  argv=[*docker,'compose','--project-directory',spec['workdir'],'--project-name',spec['project']]
  for ref in refs:
   opaque=S.OpaqueReference(ref,stack);checks.append(opaque.check)
   if ref['cli']:argv+=['--env-file',ref['path']]
  for binding in spec['compose_files']:
   need(type(binding) is dict and set(binding)=={'path','bytes','sha256'} and (binding['path'].endswith(('.yaml','.yml'))
    or Path(binding['path']).name=='rollback-app.override.json' and binding['bytes']==153),'normal_prefix')
   source=S.Held(binding['path'],stack,pin=binding['sha256'],maximum=131072,public_source=True)
   need(len(source.raw)==binding['bytes'],'normal_prefix');public_compose_source(binding,source.raw);checks.append(source.check);argv+=['--file',binding['path']]
  prefixes[role]=argv
 empty=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.normal-restore-client-',dir=Path(__file__).parent)))
 os.chmod(empty,0o700);need(empty.stat().st_uid==empty.stat().st_gid==0 and not list(empty.iterdir()),'client_configuration')
 environment={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','HOME':'/root','DOCKER_CONFIG':str(empty)}
 def command(argv,maximum=4*1024**2):
  need(time.time()<value['deadline'],'deadline')
  for check in checks:check()
  rc,raw=(capture or T.capture)(argv,min(60,max(.1,value['deadline']-time.time())),environment=environment)
  need(type(raw) is bytes and len(raw)<=maximum and rc==0,'normal_command_refused')
  return raw
 current=command([*docker,'compose','version','--short'],256).decode('ascii').strip().removeprefix('v')
 need(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?',current),'compose_version')
 with open('/etc/machine-id','rb') as source:machine=source.read(65).strip().decode()
 need(machine==value['machine_id'],'machine_identity')
 stores={kind:inspect_one(command([*docker,'container','inspect',cid])) for kind,cid in value['datastores'].items()}
 stable={store['Id']:stable_view(store) for store in stores.values()}
 aliases={kind:set() for kind in stores}
 for kind,store in stores.items():
  need(store['Id']==value['datastores'][kind] and store['State']['Running'] is True,'normal_datastore')
  for network in store['NetworkSettings']['Networks'].values():
   aliases[kind].update(x for x in network.get('Aliases') or [] if type(x) is str)
  need(aliases[kind] and len(aliases[kind])<=16,'normal_datastore')
 private={'roles':{},'datastores':stores,'aliases':aliases};scope=[]
 for role,spec in value['roles'].items():
  argv=prefixes[role]
  references=document(command([*argv,'config','--format','json','--no-env-resolution']))
  allowed={r[k] for r in spec['env_files'] for k in ('path','target')}
  need(type(references.get('services')) is dict and 0<len(references['services'])<=64,'normal_prefix')
  for service in references['services'].values():
   need(type(service) is dict and type(service.get('env_file',[])) is list,'normal_prefix')
   for ref in service.get('env_file',[]):
    path=ref['path'] if type(ref) is dict else ref;need(type(path) is str and path in allowed,'unbound_env_reference')
  model=document(command([*argv,'config','--format','json']))
  actual=inspect_one(command([*docker,'container','inspect',spec['container_id']]))
  need(actual['Id']==spec['container_id'] and actual['State']['Running'] is True,'normal_runtime')
  image=inspect_one(command([*docker,'image','inspect',actual['Image']]))
  stable[actual['Id']]=stable_view(actual)
  C.normal_baseline(model,role,row(actual),image,spec,[b['path'] for b in spec['compose_files']])
  record={**loader_scope(C,role,actual,aliases),'creation_compose_version':spec['compose_version']};scope.append(record)
  private['roles'][role]={'model':model,'actual':actual,'image':image,'scope':spec}
 for identifier,before in stable.items():
  after=inspect_one(command([*docker,'container','inspect',identifier]));need(C.same(before,stable_view(after)),'normal_runtime_changed')
 for check in checks:check()
 need(time.time()<value['deadline'],'deadline')
 public={'schema':'normal_app_restore_admission_result/1','operation':OP,'nonce':value['nonce'],'site':value['site'],
  'machine_id':machine,'observed_at':int(time.time()),'current_compose_version':current,'normal_role_eligibility':scope,
  'api_clone_started':False,'network_or_firewall_changed':False,'configuration_values_disclosed':False,
  'configuration_values_hashed':False,'security_accepted':False,'full_matrix_accepted':False}
 C.public(public);return public,private

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--control',type=Path,required=True);p.add_argument('--control-sha256',required=True);p.add_argument('--source-sha256',required=True);args=p.parse_args(argv)
 try:
  with ExitStack() as stack:
   raw,check=stack.enter_context(cold(args.control,args.control_sha256,1024**2));v=controls(document(raw),time.time())
   public,_=collect(v,stack,args.source_sha256);check()
  print(json.dumps(public,sort_keys=True));return 0
 except Exception as error:
  codes={'public_source','source_changed','json','control','deadline','role_inventory','datastore_inventory','normal_inspect',
   'fixed_command','loader_environment','endpoint_eligibility','driver_plugins','source_mounts','root_required','normal_command_refused',
   'machine_identity','normal_datastore','normal_prefix','normal_runtime','normal_runtime_changed','compose_version','client_configuration','normal_cwd',
   'env_references','default_env_reference','env_cli_binding','unbound_env_reference'}|C_CODES
  code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in codes else 'normal_admission_refused'
  known=type(error) in (RuntimeError,ValueError,TypeError,KeyError,AssertionError,OSError,PermissionError,FileNotFoundError,NotADirectoryError)
  errno=error.errno if known and isinstance(error,OSError) and type(error.errno) is int and 0<error.errno<=4095 else None
  print(json.dumps({'status':'normal_restore_admission_refused','code':code,'exception_class':type(error).__name__ if known else 'other','os_errno':errno,'configuration_values_disclosed':False,
   'security_accepted':False,'full_matrix_accepted':False}));return 1
if __name__=='__main__':raise SystemExit(main())
