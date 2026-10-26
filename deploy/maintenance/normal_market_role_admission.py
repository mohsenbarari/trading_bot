"""One-role readonly normal Market admission; complete Config stays in RAM.

No serving mutation API exists here. A later normal producer may reuse collect
in-process; its private return must never be serialized, hashed or logged.
"""
from contextlib import ExitStack,contextmanager
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,ast,json,os,re,resource,stat,sys,tempfile,time

OP='security-normal-market-admission-20261025-01'
PINS={'contract':'c07784fd6364b39db1b25f4e0e73dc95f9be6b7128b10f37f12c49dd681de140',
 'utilities':'d6c0f59c63fe8932e9c3a378eced3dbf7a993fd4caef78dd0d1c19b49d506a29',
 'market':'b30a8f63e1f385c55893eab77f635b6b67815fb2fd1d5d4366b3702d802dd540',
 'archive':'d210020e24fedd4986803a3fe2618a5016ecd99f9e3ca9588af02d77a967e17d',
 'capture':'a223222cc8bf5221aafa49fd3d69307282dce884b8e998d49fe609c56078e2a0',
 'delta':'326215cc640078afbdcb774e7ecb03cd1e7a5a91afe69175fc83780eebeebaf1',
 'rebased_sources':'2d567b9d105dbf21c490ad47587cb7c7352969ce0f7be8673814a408790dd2af',
 'rebased_validator':'ff32988c1b524abd145daac4f34d2b189900245ebeae033992dcfce44c4d82be'}
MARKER=Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
SOURCE=Path('/root/secure-envs/trading-bot/.production-runtime-source.lock')
HELPERS={'contract':'canonical_app_activation_contract.py','utilities':'canonical_app_activation_site.py','market':'normal_market_activation.py',
 'archive':'datastore_candidate_contract.py','capture':'bounded_operational_capture.py','delta':'normal_market_image_delta.py',
 'rebased_sources':'market_rebased_public_sources.py','rebased_validator':'market_rebased_proof_contract.py'}
FAILURE_CODES=set();FAILURE_TYPES={RuntimeError}
CONTAINER_FORMAT='{"Id":{{json .Id}},"Name":{{json .Name}},"Image":{{json .Image}},"Config":{{json .Config}},"HostConfig":{{json .HostConfig}},"Mounts":{{json .Mounts}},"NetworkSettings":{"Networks":{{json .NetworkSettings.Networks}}},"RestartCount":{{json .RestartCount}},"State":{"Running":{{json .State.Running}},"Pid":{{json .State.Pid}},"StartedAt":{{json .State.StartedAt}},"Restarting":{{json .State.Restarting}},"Paused":{{json .State.Paused}},"Dead":{{json .State.Dead}},"Health":{"Status":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}}}}'
IMAGE_FORMAT='{"Id":{{json .Id}},"Config":{{json .Config}},"RootFS":{{json .RootFS}},"Os":{{json .Os}},"Architecture":{{json .Architecture}}}'
def need(ok,code):
 if not ok:raise RuntimeError(code)
def identity(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
@contextmanager
def cold(path,pin,maximum=131072):
 path=Path(path);parents=[]
 for parent in path.parents:
  s=parent.lstat();need(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and not s.st_mode&0o022,'market_public_source');parents.append((parent,identity(s)[:5]))
 before=identity(path.lstat());need(path.resolve(strict=True)==path and before[2] in (0o100600,0o100644) and before[3:6]==[0,0,1] and 0<before[6]<=maximum,'market_public_source')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  need(before==identity(os.fstat(fd)),'market_public_source');raw=os.pread(fd,before[6]+1,0);need(len(raw)==before[6] and sha256(raw).hexdigest()==pin,'market_public_source')
  def check():
   need(before==identity(os.fstat(fd))==identity(path.lstat()) and os.pread(fd,len(raw)+1,0)==raw
    and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==v for p,v in parents),'market_source_changed')
  primary=False;check()
  try:yield raw,check
  except BaseException:primary=True;raise
  finally:
   try:check()
   except BaseException:
    if not primary:raise
 finally:os.close(fd)
def module(raw,path):
 for node in ast.walk(ast.parse(raw)):
  if not isinstance(node,ast.Call):continue
  name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ''
  index=1 if name in {'need','require'} else 0 if name in {'RuntimeError','Refusal'} else None
  if index is not None and len(node.args)>index and isinstance(node.args[index],ast.Constant):
   code=node.args[index].value
   if type(code) is str and re.fullmatch('[a-z][a-z0-9_]{2,80}',code):FAILURE_CODES.add(code)
 m=ModuleType('_normal_market_readonly_'+Path(path).stem);m.__file__=str(path);exec(compile(raw,str(path),'exec'),m.__dict__);return m
def document(raw):
 need(type(raw) is bytes and 0<len(raw)<=4*1024**2,'market_json')
 def pairs(rows):
  out={}
  for key,value in rows:need(key not in out,'market_json');out[key]=value
  return out
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False,'market_json'))
def controls(value,now):
 need(type(value) is dict and set(value)=={'schema','operation','nonce','site','role','machine_id','issued_at','deadline','helpers','scope','fences','evidence'}
  and value['schema']=='normal_market_role_admission/1' and value['operation']==OP,'market_control')
 need(type(value['nonce']) is str and re.fullmatch('[a-f0-9]{32}',value['nonce']) and value['site'] in {'bot','web'}
  and type(value['role']) is str and type(value['machine_id']) is str and re.fullmatch('[a-f0-9]{32}',value['machine_id']),'market_control')
 need(type(value['issued_at']) is type(value['deadline']) is int and value['issued_at']<=now<value['deadline']<=value['issued_at']+300,'market_deadline')
 need(type(value['helpers']) is dict and set(value['helpers'])==set(PINS),'market_control')
 for key,row in value['helpers'].items():need(type(row) is dict and set(row)=={'path','sha256'} and row['sha256']==PINS[key] and type(row['path']) is str
  and Path(row['path']).is_absolute() and Path(row['path']).name==HELPERS[key],'market_control')
 need(type(value['evidence']) is dict and set(value['evidence'])=={'catalog','profile_evidence','stack','load_receipt','load_control','archive_admission','rebased_proof'},'market_control')
 for name,row in value['evidence'].items():
  if row is None:need(name=='rebased_proof' and value['role']!='market-capture-account2','market_control');continue
  need(type(row) is dict and set(row)=={'path','sha256','bytes'} and type(row['path']) is str and Path(row['path']).is_absolute()
   and type(row['sha256']) is str and re.fullmatch('[a-f0-9]{64}',row['sha256']) and type(row['bytes']) is int and 0<row['bytes']<=2*1024**2,'market_control')
 return value
def scope_control(C,M,value):
 role=value['role'];scope=value['scope'];need(role in M.ROLES and M.ROLES[role][0]==value['site'],'market_role')
 fields={'project','workdir','container_name','compose_files','profiles','env_files','secret_files','public_sources','compose_version','docker_version','environment_file_label','release','baseline','image_model'}
 need(type(scope) is dict and set(scope)==fields and scope['image_model']=='authenticated_ci_rebuild','market_scope')
 C.absolute(scope['workdir']);need(type(scope['project']) is str and re.fullmatch('[a-z0-9][a-z0-9_-]{0,99}',scope['project'])
  and type(scope['container_name']) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{1,127}',scope['container_name'])
  and C.same(scope['profiles'],['foundation-template',value['site']]),'market_scope')
 need(type(scope['compose_files']) is list and 2<=len(scope['compose_files'])<=24 and len({r['path'] for r in scope['compose_files']})==len(scope['compose_files']),'market_prefix')
 for row in scope['compose_files']:
  need(type(row) is dict and set(row)=={'path','sha256','bytes'} and type(row['bytes']) is int and 0<row['bytes']<=131072 and C.hex64(row['sha256']),'market_prefix')
  C.absolute(row['path']);need(row['path'].endswith(('.yml','.yaml')),'market_prefix')
 for name in ('env_files','secret_files'):
  rows=scope[name];need(type(rows) is list and len(rows)<=64 and (rows or name=='secret_files') and len({r['path'] for r in rows})==len(rows),'market_references')
  for row in rows:
   need(type(row) is dict and set(row)=={'path','target','identity','aliases','cli'} and type(row['cli']) is bool and (name=='env_files' or row['cli'] is False),'market_references')
   C.absolute(row['path']);C.absolute(row['target']);(C.metadata if name=='env_files' else M.secret_metadata)(row['identity'])
   need(type(row['aliases']) is list and len(row['aliases'])<=8,'market_references')
   for alias in row['aliases']:
    need(type(alias) is dict and set(alias)=={'path','identity'},'market_references');C.absolute(alias['path']);ident=alias['identity']
    need(type(ident) is list and len(ident)==9 and all(type(n) is int for n in ident) and ident[2]==0o120777 and ident[3]==0
     and ident[4] in ({0} if name=='env_files' else {0,10001}) and ident[5]==1 and 0<ident[6]<=4096,'market_references')
 cli=[r['path'] for r in scope['env_files'] if r['cli']]
 need(cli or any(r['path']==scope['workdir']+'/.env' for r in scope['env_files']),'market_default_env_reference')
 need((not cli and scope['environment_file_label'] is None) or (cli and all(',' not in p for p in cli) and scope['environment_file_label']==','.join(cli)),'market_env_cli')
 need(type(scope['compose_version']) is str and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?',scope['compose_version'])
  and type(scope['docker_version']) is str and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',scope['docker_version'])
  and type(scope['release']) is str and re.fullmatch('[a-f0-9]{40}',scope['release']),'market_scope')
 sources=scope['public_sources'];need(type(sources) is list and len(sources)<=32 and len({r['target'] for r in sources})==len(sources),'market_sources')
 for row in sources:
  C.absolute(row['target']);need(type(row) is dict and row.get('kind') in {'bind','image-baked-rebase'} and C.hex64(row['sha256'])
   and type(row['bytes']) is int and 0<row['bytes']<=131072,'market_sources')
  if row['kind']=='bind':need(set(row)=={'kind','path','target','bytes','sha256'},'market_sources');C.absolute(row['path'])
  else:need(set(row)=={'kind','target','bytes','sha256'} and role=='market-capture-account2','market_sources')
 if role=='market-capture-account2':need({r['target']:(r['bytes'],r['sha256']) for r in sources}==M.F79_SOURCES and all(r['kind']=='image-baked-rebase' for r in sources),'market_sources')
 need(type(value['fences']) is dict and set(value['fences'])==({'marker','source'} if value['site']=='bot' else {'marker'}),'market_fences')
 for key,ident in value['fences'].items():C.metadata(ident,empty=key=='source')
 if value['site']=='bot':need(C.same(value['fences']['source'],M.SOURCE_ID),'market_fences')
 baseline=scope['baseline'];need(type(baseline) is dict and set(baseline)=={'container_id','image_id','pid','started','restarts'}
  and C.hex64(baseline['container_id']) and C.image_id(baseline['image_id']) and type(baseline['pid']) is int and baseline['pid']>0
  and type(baseline['started']) is str and baseline['started'] and type(baseline['restarts']) is int and baseline['restarts']>=0,'market_baseline')
 return scope

def collect(value,stack,self_pin,*,capture=None):
 """Return (closed public observation, private RAM). Never emit item two."""
 need(os.geteuid()==os.getegid()==0,'market_root');controls(value,time.time());checks=[];modules={}
 _,check=stack.enter_context(cold(Path(__file__),self_pin));checks.append(check)
 for name,row in value['helpers'].items():
  raw,check=stack.enter_context(cold(row['path'],PINS[name]));checks.append(check);modules[name]=module(raw,row['path'])
 C,S,M,D,T,X,R,W=(modules[k] for k in ('contract','utilities','market','archive','capture','delta','rebased_sources','rebased_validator'));S.C=C
 FAILURE_TYPES.add(D.Refusal)
 scope=scope_control(C,M,value);role=value['role'];workdir=Path(scope['workdir']);need(Path.cwd().resolve(strict=True)==workdir.resolve(strict=True),'market_cwd')
 before=identity(workdir.lstat());need(workdir.resolve(strict=True)==workdir and stat.S_ISDIR(before[2]) and before[3:5]==[0,0] and not before[2]&0o022,'market_cwd')
 def cwd_check():need(before==identity(workdir.lstat()) and workdir.resolve(strict=True)==workdir and Path.cwd().resolve(strict=True)==workdir,'market_cwd')
 checks.append(cwd_check)
 def final_cwd():
  primary=sys.exc_info()[0] is not None
  try:cwd_check()
  except BaseException:
   if not primary:raise
 # Runs after child reference/evidence finalizers, before source libraries.
 stack.callback(final_cwd)
 for name,path in (('marker',MARKER),*([('source',SOURCE)] if value['site']=='bot' else [])):
  fence=S.Held(path,stack,expected=value['fences'][name],opaque=True,lock=True);checks.append(fence.check)
 docker=['/usr/bin/docker','--host=unix:///var/run/docker.sock'];argv=[*docker,'compose','--project-directory',scope['workdir'],'--project-name',scope['project']]
 # The inherited template declaration never enables that profile on the CLI.
 argv+=['--profile',value['site']]
 for ref in scope['env_files']:
  held=S.OpaqueReference(ref,stack);checks.append(held.check)
  if ref['cli']:argv+=['--env-file',ref['path']]
 for ref in scope['secret_files']:
  held=M.SecretReference(ref,S,stack);checks.append(held.check)
 for proof in scope['compose_files']:
  held=S.Held(proof['path'],stack,pin=proof['sha256'],maximum=131072,public_source=True);need(len(held.raw)==proof['bytes'],'market_prefix');checks.append(held.check);argv+=['--file',proof['path']]
 for proof in scope['public_sources']:
  if proof['kind']=='bind':
   held=S.Held(proof['path'],stack,pin=proof['sha256'],maximum=131072,public_source=True);need(len(held.raw)==proof['bytes'],'market_sources');checks.append(held.check)
 evidence={}
 for key,row in value['evidence'].items():
  if row is None:continue
  raw,check=stack.enter_context(cold(row['path'],row['sha256'],2*1024**2));need(len(raw)==row['bytes'],'market_evidence');checks.append(check);evidence[key]=raw
 data=X.lineage(evidence['catalog'],evidence['profile_evidence'],evidence['stack'],role)
 local,receipt,admitted=X.admit_load(data,evidence['load_receipt'],evidence['load_control'],evidence['archive_admission'],value['site'],value['machine_id'])
 need(Path(__file__).parent.resolve(strict=True)!=workdir,'market_client_config')
 empty=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='.normal-market-client-',dir=Path(__file__).parent)));os.chmod(empty,0o700)
 need(empty.stat().st_uid==empty.stat().st_gid==0 and not list(empty.iterdir()),'market_client_config')
 environment={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C.UTF-8','LANG':'C.UTF-8','HOME':'/root','DOCKER_CONFIG':str(empty)}
 def command(args,maximum=4*1024**2):
  need(time.time()<value['deadline'],'market_deadline')
  for check in checks:check()
  rc,raw=(capture or T.capture)(args,min(60,max(.1,value['deadline']-time.time())),environment=environment)
  need(rc==0 and type(raw) is bytes and len(raw)<=maximum,'market_readonly_command');return raw
 def one(args):
  row=document(command(args));need(type(row) is dict,'market_inspect');return row
 def container(cid):return one([*docker,'container','inspect','--format',CONTAINER_FORMAT,cid])
 def image(iid):
  row=one([*docker,'image','inspect','--format',IMAGE_FORMAT,iid]);need(set(row)=={'Id','Config','RootFS','Os','Architecture'} and row['Os']=='linux' and row['Architecture']=='amd64','market_inspect');return row
 with open('/etc/machine-id','rb') as source:machine=source.read(65).strip().decode()
 need(machine==value['machine_id'],'market_machine')
 current=command([*docker,'compose','version','--short'],1024).decode().strip().removeprefix('v')
 need(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?',current),'market_compose_version')
 version=command([*docker,'version','--format','{{.Server.Version}}'],1024).decode().strip();need(version==scope['docker_version'],'market_docker_version')
 refs=document(command([*argv,'config','--format','json','--no-env-resolution']));allowed={p for ref in scope['env_files'] for p in (ref['path'],ref['target'])}
 need(type(refs.get('services')) is dict and 0<len(refs['services'])<=64,'market_prefix')
 for service in refs['services'].values():
  need(type(service) is dict and type(service.get('env_file',[])) is list,'market_prefix')
  for ref in service.get('env_file',[]):need((ref['path'] if type(ref) is dict else ref) in allowed,'market_unbound_env')
 model=document(command([*argv,'config','--format','json']));need(model.get('name')==scope['project'] and role in model['services'],'market_prefix')
 need('pull_policy' not in model['services'][role] or type(model['services'][role]['pull_policy']) is str and model['services'][role]['pull_policy']=='never','normal_pull_policy')
 actual=container(scope['baseline']['container_id']);row=S.row(actual);S.stable_running(row)
 need(all(C.same(row[k],scope['baseline'][v]) for k,v in (('id','container_id'),('image','image_id'),('pid','pid'),('started','started'),('restarts','restarts'))),'market_baseline')
 old=image(row['image']);candidate=image(local);delta=X.image_delta(D,data,old,candidate,local)
 historical='com.docker.compose.replace' in row['config']['Labels']
 M.baseline(C,model,role,row,old,scope,[r['path'] for r in scope['compose_files']],replacement=historical)
 if role=='market-capture-account2':
  proof=document(evidence['rebased_proof']);lineage=R.admit_lineage(evidence['profile_evidence'],data['spec'])
  association={'catalog_sha256':X.CATALOG_SHA,'load_receipt_sha256':value['evidence']['load_receipt']['sha256'],
   'load_control_sha256':value['evidence']['load_control']['sha256'],'source_admission_sha256':value['evidence']['archive_admission']['sha256'],
   'producer_sha256':PINS['rebased_sources'],'archive_sha256':data['spec']['tar_sha256'],'candidate_config_sha256':data['spec']['candidate_config_id'][7:]}
  W.admit(R,proof,lineage,data['spec'],admitted,association,old['RootFS']['Layers'])
  fresh=R.observe_old_fixed(lambda args:command([*docker,*args],16384),row['id'],old['Id'],proof['candidate_effective_metadata'])
  need(fresh['runtime']['pid']==row['pid'],'market_baseline')
 after=container(row['id']);later=S.row(after);S.stable_running(later)
 need(C.same(S.comparable_inventory({row['id']:row}),S.comparable_inventory({later['id']:later})),'market_runtime_changed')
 need(C.same(old,image(old['Id'])) and C.same(candidate,image(local)),'market_image_changed')
 for check in checks:check()
 statuses=[r['health'] if r['health'] in (None,'starting','healthy','unhealthy') else 'unknown' for r in (row,later)]
 public={'schema':'normal_market_role_admission_result/1','operation':OP,'nonce':value['nonce'],'role':role,'site':value['site'],
  'machine_id':machine,'observed_at':int(time.time()),'current_compose_version':current,'creation_compose_version':scope['compose_version'],
  'current_docker_version':version,'historical_recreate_metadata_observed':historical,'health_observations':statuses,'runtime_health_issues_open':True,
  'image_delta':delta,'normal_prefix_and_complete_current_config_admitted':True,'rebased_public_sources_admitted':role=='market-capture-account2',
  'serving_replacement_permitted':False,'backup_restore_proven':False,'cross_site_activation_fences_held':False,'functional_progress_proven':False,
  'serving_changed':False,'configuration_values_disclosed':False,'configuration_values_hashed':False,'security_accepted':False,'full_matrix_accepted':False}
 C.public(public)
 return public,{'model':model,'actual':actual,'old_image':old,'candidate_image':candidate,'scope':scope,'image_evidence':data,'load_receipt':receipt,'archive_admission':admitted}

def main(argv=None):
 try:
  parser=argparse.ArgumentParser(description=__doc__,add_help=False);parser.add_argument('--control',type=Path,required=True);parser.add_argument('--control-sha256',required=True);parser.add_argument('--source-sha256',required=True)
  parser.error=lambda _:need(False,'market_control')
  args=parser.parse_args(argv)
  need(resource.getrlimit(resource.RLIMIT_AS)==(resource.RLIM_INFINITY,resource.RLIM_INFINITY),'market_address_space')
  with ExitStack() as stack:
   raw,check=stack.enter_context(cold(args.control,args.control_sha256,1024**2));value=controls(document(raw),time.time());public,_=collect(value,stack,args.source_sha256);check()
  print(json.dumps(public,sort_keys=True));return 0
 except Exception as error:
  # Closed literal codes only; error arguments, client output and Config are
  # never printed. Unknown failures carry no implied retry or authority.
  allowed={'market_public_source','market_source_changed','market_json','market_control','market_deadline','market_role','market_scope','market_prefix','market_references',
   'market_default_env_reference','market_env_cli','market_sources','market_fences','market_baseline','market_root','market_cwd','market_evidence','market_client_config',
   'market_readonly_command','market_inspect','market_machine','market_compose_version','market_docker_version','market_unbound_env','market_runtime_changed','market_image_changed',
   'normal_pull_policy','market_catalog','market_profile','market_zip','market_evidence_inventory','market_evidence_bound','market_export','market_raw_report','market_config_identity',
   'market_composition','market_false_claims','market_e46_lineage','market_payload','market_profile_lineage','market_old_public_config','market_rebuild_model','market_load_control',
   'market_load_receipt','market_archive_receipt','market_local_identity','market_daemon_identity','market_candidate_config','market_image_defaults','market_image_default_policy','market_business_image_config'}
  code=error.args[0] if type(error) in FAILURE_TYPES and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in allowed|FAILURE_CODES else 'normal_market_admission_refused'
  known=type(error) in FAILURE_TYPES|{ValueError,TypeError,KeyError,AssertionError,OSError,PermissionError,FileNotFoundError,NotADirectoryError}
  number=error.errno if known and isinstance(error,OSError) and type(error.errno) is int and 0<error.errno<=4095 else None
  print(json.dumps({'status':'normal_market_admission_refused','code':code,'exception_class':type(error).__name__ if known else 'other','os_errno':number,
   'serving_changed':False,'configuration_values_disclosed':False,'security_accepted':False,'retry_allowed':False}));return 1
if __name__=='__main__':raise SystemExit(main())
